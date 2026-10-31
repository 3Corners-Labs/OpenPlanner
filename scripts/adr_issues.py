"""One GitHub issue per open build step of an ADR, and a report of the drift (ADR 0078 point 6).

The product owner kept finding decisions that were written down and never built. Every ADR
from 0059 on carries a *Build status* table (ADR 0059 point 7); this script turns its open rows
into work that is visible where work is tracked:

- **Sync** (the default): for every row whose state is ``planned`` or ``follow-up`` an open
  issue exists, titled ``ADR NNNN step S: <What>`` and labelled ``adr:NNNN`` (the label is
  created when missing). A row that moved to ``merged``, ``built`` or ``live`` closes its
  issue; a row ``in PR #N`` leaves the issue to that pull request (``Closes #N``). Issues
  without an ``adr:`` label are never touched, and running the sync twice changes nothing.
- **Report** (``--report``): one Markdown report of what no longer agrees: a row of
  ``docs/adr-status.md`` whose category contradicts the ADR's table (BUILT with open rows,
  NOT BUILT with merged rows), rows that say ``in PR #N`` for a pull request that is merged or
  closed, and issues labelled ``adr:`` whose row no longer exists. With ``--post`` the report
  becomes the one open issue ``ADR drift <date>``, updated in place and closed when empty.

``.github/workflows/adr-drift.yml`` runs both weekly and after a merge to ``main`` that
changes an ADR. ``--dry-run`` prints what the sync would do without writing anything; the
exit code is non-zero only when a table or the overview cannot be read.

Everything that talks to GitHub goes through :class:`GitHub`, a thin layer over the ``gh``
CLI, so the logic is pure and tested with a fake, and moving to another forge means replacing
that one class (ADR 0078, portability).
"""

import argparse
import json
import re
import subprocess  # noqa: S404 - runs gh with fixed arguments only
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LABEL_PREFIX = "adr:"
"""The label of every issue this script owns: ``adr:NNNN`` for a build step."""

LABEL_COLOUR = "5319e7"
REPORT_TITLE = "ADR drift"
REPORT_LABEL = "adr:drift"
"""The label of the one report issue, so it is never mistaken for a build step."""

STATES = ("planned", "in PR", "merged", "built", "live", "follow-up")
"""What a *Build status* row may start its state with (ADR 0059 point 7)."""

OPEN_STATES = frozenset({"planned", "follow-up"})
DONE_STATES = frozenset({"merged", "built", "live"})
IN_PR = "in PR"

CATEGORIES = ("BUILT", "PARTIAL", "NOT BUILT", "POLICY")
"""What a *Built?* cell of ``docs/adr-status.md`` starts with."""

_HEADING = re.compile(r"^# (\d{4})\.\s*(.+?)\s*$", re.M)
_BUILD_STATUS = re.compile(r"^## Build status\n(.*?)(?=^## |\Z)", re.M | re.S)
_OVERVIEW_ROW = re.compile(r"^\| (\d{4}) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")
_ISSUE_TITLE = re.compile(r"^ADR (\d{4}) step (\S+): ")
_PR_NUMBER = re.compile(r"#(\d+)")


class ParseError(ValueError):
    """A *Build status* table or the overview says something the agreed vocabulary lacks."""


@dataclass(frozen=True)
class Step:
    """One row of an ADR's *Build status* table."""

    adr: str
    title: str
    file: str
    step: str
    what: str
    pr: str
    state: str

    @property
    def status(self) -> str:
        """The agreed state word the State cell starts with."""
        found = next((s for s in STATES if self.state.startswith(s)), None)
        if found is None:
            raise ParseError(
                f"{self.file} step {self.step}: state {self.state!r} is none of {', '.join(STATES)}"
            )
        return found

    @property
    def key(self) -> tuple[str, str]:
        """What identifies the row across renames of its text: ADR number and step."""
        return (self.adr, self.step)

    @property
    def is_the_adr(self) -> bool:
        """Whether the row is step 0, the ADR itself (``ADR proposed``), not a build step."""
        return self.step == "0"

    @property
    def issue_title(self) -> str:
        """The title of the issue that tracks this row."""
        return f"ADR {self.adr} step {self.step}: {self.what}"

    @property
    def label(self) -> str:
        """The label of the issue that tracks this row."""
        return f"{LABEL_PREFIX}{self.adr}"

    @property
    def pr_number(self) -> int | None:
        """The pull request number in the PR or State cell, if any."""
        found = _PR_NUMBER.search(self.pr + " " + self.state)
        return int(found.group(1)) if found else None


@dataclass(frozen=True)
class Issue:
    """A GitHub issue as ``gh issue list`` reports it."""

    number: int
    title: str
    state: str
    labels: frozenset[str]

    @property
    def adr_label(self) -> str | None:
        """The ``adr:`` label on the issue, or ``None`` when it is not one of ours."""
        return next(
            (label for label in sorted(self.labels) if label.startswith(LABEL_PREFIX)), None
        )

    @property
    def key(self) -> tuple[str, str] | None:
        """The ADR number and step the title names, or ``None`` when it does not."""
        found = _ISSUE_TITLE.match(self.title)
        return (found.group(1), found.group(2)) if found else None


@dataclass(frozen=True)
class Action:
    """One change the sync makes: ``create``, ``close``, ``reopen`` or ``retitle``."""

    kind: str
    step: Step | None
    issue: Issue | None

    def __str__(self) -> str:
        """Say what the action does, for the log and for ``--dry-run``."""
        assert self.step is not None  # noqa: S101 - every kind carries its row
        if self.kind == "create":
            return f"create: {self.step.issue_title} (label {self.step.label})"
        assert self.issue is not None  # noqa: S101 - the other kinds act on an issue
        if self.kind == "retitle":
            return f"retitle #{self.issue.number}: {self.step.issue_title}"
        return f"{self.kind} #{self.issue.number}: {self.issue.title} (row says {self.step.status})"


# --- parsing -----------------------------------------------------------------


def parse_adr(text: str, file: str) -> list[Step]:
    """Return the rows of one ADR's *Build status* table.

    Args:
        text: The ADR's full text.
        file: Its file name, for messages and the issue body.

    Returns:
        One :class:`Step` per row; an empty list for an ADR without a table.

    Raises:
        ParseError: A row's state is none of the agreed states.
    """
    heading = _HEADING.search(text)
    number = heading.group(1) if heading else file[:4]
    title = heading.group(2) if heading else file[5:-3]
    section = _BUILD_STATUS.search(text)
    if section is None:
        return []
    steps = []
    for line in section.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if (
            not line.startswith("|")
            or len(cells) < 4
            or cells[0] == "Step"
            or set(cells[0]) == {"-"}
        ):
            continue
        step = Step(number, title, file, cells[0], cells[1], cells[-2], cells[-1])
        step.status  # noqa: B018 - raises ParseError on an unknown state
        steps.append(step)
    return steps


def read_steps(adr_dir: Path) -> list[Step]:
    """Return the rows of every ADR's *Build status* table under ``adr_dir``."""
    return [
        step
        for adr in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
        for step in parse_adr(adr.read_text(), adr.name)
    ]


def parse_overview(text: str) -> dict[str, str]:
    """Return the category per ADR number from ``docs/adr-status.md``.

    Raises:
        ParseError: A *Built?* cell starts with none of the agreed categories.
    """
    categories = {}
    for line in text.splitlines():
        row = _OVERVIEW_ROW.match(line.strip())
        if row is None:
            continue
        number, built = row.group(1), row.group(3)
        category = next((c for c in CATEGORIES if built.startswith(c)), None)
        if category is None:
            raise ParseError(f"docs/adr-status.md: ADR {number} is {built!r}, none of {CATEGORIES}")
        categories[number] = category
    return categories


# --- the sync ----------------------------------------------------------------


def _ours(issues: Iterable[Issue]) -> dict[tuple[str, str], Issue]:
    """The issue per row key, open before closed, lowest number first."""
    by_key: dict[tuple[str, str], Issue] = {}
    for issue in sorted(issues, key=lambda i: (i.state != "OPEN", i.number)):
        if (
            issue.adr_label
            and issue.adr_label != REPORT_LABEL
            and issue.key
            and issue.key not in by_key
        ):
            by_key[issue.key] = issue
    return by_key


def plan(steps: Iterable[Step], issues: Iterable[Issue]) -> list[Action]:
    """Return what the sync changes so that the issues match the tables.

    Args:
        steps: Every row of every *Build status* table.
        issues: Every issue of the repository, open and closed.

    Returns:
        Nothing when they already agree: the sync is idempotent.
    """
    by_key = _ours(issues)
    actions = []
    for step in steps:
        issue = by_key.get(step.key)
        if step.is_the_adr:
            continue
        if step.status in OPEN_STATES:
            if issue is None:
                actions.append(Action("create", step, None))
            elif issue.state != "OPEN":
                actions.append(Action("reopen", step, issue))
            elif issue.title != step.issue_title:
                actions.append(Action("retitle", step, issue))
        elif step.status in DONE_STATES and issue is not None and issue.state == "OPEN":
            actions.append(Action("close", step, issue))
    return actions


def issue_body(step: Step) -> str:
    """The body of a build step's issue: where the row is and how the issue closes."""
    return (
        f"ADR {step.adr} *{step.title}*, build step {step.step}: {step.what}\n\n"
        f"Source: `docs/adr/{step.file}`, section *Build status*; the row says `{step.status}`.\n\n"
        "The pull request that builds this step sets the row to `in PR #N` and closes this issue "
        "with `Closes #N` in its description. `scripts/adr_issues.py` closes it as well once the "
        "row says `merged`, `built` or `live`, and reopens it while the row stays open "
        "(ADR 0078 point 6): the table in the ADR is the source of truth, so change the row, not "
        "the issue."
    )


# --- drift -------------------------------------------------------------------


@dataclass
class Drift:
    """What no longer agrees between the tables, the overview and the issues."""

    overview: list[str] = field(default_factory=list)
    stale_prs: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Whether there is nothing to report."""
        return not (self.overview or self.stale_prs or self.orphans)


def drift(
    steps: Sequence[Step],
    overview: dict[str, str],
    issues: Iterable[Issue],
    pr_state: Callable[[int], str],
) -> Drift:
    """Compare the *Build status* tables with the overview and the issues.

    Args:
        steps: Every row of every *Build status* table.
        overview: The category per ADR from ``docs/adr-status.md``.
        issues: Every issue of the repository.
        pr_state: Gives ``OPEN``, ``MERGED``, ``CLOSED`` or ``UNKNOWN`` for a pull request.

    Returns:
        The drift, one line per finding, in the order of the ADRs.
    """
    found = Drift()
    for step in steps:
        category = overview.get(step.adr)
        disagrees = (category == "BUILT" and step.status not in DONE_STATES) or (
            category == "NOT BUILT" and step.status in DONE_STATES and not step.is_the_adr
        )
        if disagrees:
            found.overview.append(
                f"ADR {step.adr} is {category} in `docs/adr-status.md`, "
                f"but step {step.step} is `{step.status}`"
            )
        if step.status == IN_PR and step.pr_number is not None:
            state = pr_state(step.pr_number)
            if state in ("MERGED", "CLOSED"):
                found.stale_prs.append(
                    f"ADR {step.adr} step {step.step} says `in PR` #{step.pr_number}, "
                    f"but that pull request is {state.lower()}"
                )
    files = {step.adr: step.file for step in steps}
    keys = {step.key for step in steps}
    seen: dict[tuple[str, str], Issue] = {}
    for issue in sorted((i for i in issues if i.state == "OPEN"), key=lambda i: i.number):
        if issue.adr_label is None or issue.adr_label == REPORT_LABEL:
            continue
        key = issue.key
        if key is None:
            found.orphans.append(
                f"#{issue.number} `{issue.title}`: labelled `{issue.adr_label}` but not titled "
                "`ADR NNNN step S: …`"
            )
        elif key in seen:
            first = seen[key].number
            found.orphans.append(
                f"#{issue.number} `{issue.title}`: a second issue for the row of #{first}"
            )
        elif key not in keys:
            where = f"docs/adr/{files[key[0]]}" if key[0] in files else f"docs/adr/{key[0]}-*.md"
            found.orphans.append(f"#{issue.number} `{issue.title}`: no such row in `{where}`")
        else:
            seen[key] = issue
    return found


def render(found: Drift, today: date) -> str:
    """Render the drift as the Markdown of the report issue."""
    lines = [f"# {REPORT_TITLE} {today.isoformat()}", ""]
    if found.empty:
        lines += [
            "Nothing drifts: the Build status tables, `docs/adr-status.md` and the",
            "`adr:` issues agree.",
        ]
        return "\n".join(lines) + "\n"
    lines += [
        "The code wins over every table (CLAUDE.md): fix the row, the overview or close the issue.",
        "Generated by `scripts/adr_issues.py --report` (ADR 0078 point 6).",
    ]
    sections = (
        ("Overview disagrees with a Build status table", found.overview),
        ("Rows that say in PR for a pull request that is gone", found.stale_prs),
        ("Issues without a row", found.orphans),
    )
    for heading, items in sections:
        if items:
            lines += ["", f"## {heading}", "", *(f"- {item}" for item in items)]
    return "\n".join(lines) + "\n"


# --- the gh layer ------------------------------------------------------------

Runner = Callable[[Sequence[str]], str]
"""Runs ``gh`` with the given arguments and returns its standard output."""


def run_gh(args: Sequence[str]) -> str:
    """Run the ``gh`` CLI with fixed arguments; ``GH_TOKEN`` or ``gh auth`` provides the token."""
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["gh", *args],  # noqa: S607 - gh from PATH, as CI and every developer run it
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class GitHub:
    """The only place that talks to GitHub: reads and writes through ``gh``."""

    def __init__(self, run: Runner = run_gh) -> None:
        """Keep the runner; tests pass a fake."""
        self._run = run

    def labels(self) -> set[str]:
        """Every label of the repository."""
        rows = json.loads(self._run(["label", "list", "--json", "name", "--limit", "500"]))
        return {str(row["name"]) for row in rows}

    def issues(self) -> list[Issue]:
        """Every issue of the repository, open and closed (pull requests are not issues here)."""
        rows = json.loads(
            self._run(
                [
                    "issue",
                    "list",
                    "--state",
                    "all",
                    "--limit",
                    "1000",
                    "--json",
                    "number,title,state,labels",
                ]
            )
        )
        return [
            Issue(
                int(row["number"]),
                str(row["title"]),
                str(row["state"]),
                frozenset(str(label["name"]) for label in row["labels"]),
            )
            for row in rows
        ]

    def pr_state(self, number: int) -> str:
        """``OPEN``, ``MERGED`` or ``CLOSED``; ``UNKNOWN`` when gh cannot see the pull request."""
        try:
            return str(
                json.loads(self._run(["pr", "view", str(number), "--json", "state"]))["state"]
            )
        except (subprocess.CalledProcessError, KeyError, ValueError):
            return "UNKNOWN"

    def create_label(self, name: str, description: str) -> None:
        """Create a label."""
        self._run(["label", "create", name, "--color", LABEL_COLOUR, "--description", description])

    def create_issue(self, title: str, body: str, label: str) -> int:
        """Create an issue and return its number (gh prints its URL)."""
        url = self._run(["issue", "create", "--title", title, "--body", body, "--label", label])
        return int(url.strip().rsplit("/", 1)[-1])

    def close_issue(self, number: int, comment: str) -> None:
        """Close an issue with a comment that says why."""
        self._run(["issue", "close", str(number), "--comment", comment])

    def reopen_issue(self, number: int, comment: str) -> None:
        """Reopen an issue with a comment that says why."""
        self._run(["issue", "reopen", str(number), "--comment", comment])

    def retitle(self, number: int, title: str) -> None:
        """Give an issue a new title."""
        self._run(["issue", "edit", str(number), "--title", title])

    def edit_issue(self, number: int, title: str, body: str) -> None:
        """Replace an issue's title and body."""
        self._run(["issue", "edit", str(number), "--title", title, "--body", body])


# --- commands ----------------------------------------------------------------


def _say(dry_run: bool, what: str) -> None:
    print(f"would {what}" if dry_run else what)


def _ensure_label(gh: GitHub, labels: set[str], name: str, description: str, dry_run: bool) -> None:
    if name in labels:
        return
    _say(dry_run, f"create label {name}")
    if not dry_run:
        gh.create_label(name, description)
    labels.add(name)


def sync(gh: GitHub, steps: Sequence[Step], dry_run: bool) -> None:
    """Make the issues match the tables (or say what would change)."""
    labels = gh.labels()
    actions = plan(steps, gh.issues())
    if not actions:
        print("nothing to do: the issues match the Build status tables")
        return
    for action in actions:
        step, issue = action.step, action.issue
        assert step is not None  # noqa: S101 - plan() fills it
        if action.kind == "create":
            _ensure_label(gh, labels, step.label, f"Build steps of ADR {step.adr}", dry_run)
        _say(dry_run, str(action))
        if dry_run:
            continue
        why = (
            f"The Build status row in `docs/adr/{step.file}` says `{step.state}` "
            "(ADR 0078 point 6)."
        )
        if action.kind == "create":
            gh.create_issue(step.issue_title, issue_body(step), step.label)
        elif action.kind == "close" and issue is not None:
            gh.close_issue(issue.number, why)
        elif action.kind == "reopen" and issue is not None:
            gh.reopen_issue(issue.number, why)
        elif action.kind == "retitle" and issue is not None:
            gh.retitle(issue.number, step.issue_title)


def report(
    gh: GitHub,
    steps: Sequence[Step],
    overview: dict[str, str],
    today: date,
    post: bool,
    dry_run: bool,
) -> None:
    """Print the drift report and, with ``post``, keep it as the one open report issue."""
    issues = gh.issues()
    found = drift(steps, overview, issues, gh.pr_state)
    text = render(found, today)
    print(text, end="")
    if not post:
        return
    title = f"{REPORT_TITLE} {today.isoformat()}"
    existing = next(
        (
            i
            for i in sorted(issues, key=lambda i: i.number)
            if i.state == "OPEN" and i.title.startswith(REPORT_TITLE)
        ),
        None,
    )
    if found.empty:
        if existing is not None:
            _say(dry_run, f"close #{existing.number}: nothing drifts on {today.isoformat()}")
            if not dry_run:
                gh.close_issue(
                    existing.number,
                    f"Nothing drifts on {today.isoformat()}; closed by the drift check.",
                )
        return
    if existing is not None:
        _say(dry_run, f"update #{existing.number}: {title}")
        if not dry_run:
            gh.edit_issue(existing.number, title, text)
        return
    _ensure_label(gh, gh.labels(), REPORT_LABEL, "The weekly ADR drift report", dry_run)
    _say(dry_run, f"create: {title}")
    if not dry_run:
        gh.create_issue(title, text, REPORT_LABEL)


def main(argv: Sequence[str] | None = None, root: Path = ROOT, run: Runner = run_gh) -> int:
    """Sync the issues, or print (and post) the drift report; exit 2 when a table cannot be read."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--report", action="store_true", help="print the drift report instead of syncing"
    )
    parser.add_argument(
        "--post", action="store_true", help="with --report: keep it as the issue 'ADR drift <date>'"
    )
    parser.add_argument(
        "--counts", action="store_true", help="print how many ADRs are in each category and stop"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would change; write nothing"
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="the report's date",
    )
    args = parser.parse_args(argv)

    if args.counts:
        try:
            overview = parse_overview((root / "docs" / "adr-status.md").read_text())
        except ParseError as error:
            print(f"::error::{error}")
            return 2
        for category in CATEGORIES:
            print(f"{category}: {sum(1 for found in overview.values() if found == category)}")
        return 0

    gh = GitHub(run)
    try:
        steps = read_steps(root / "docs" / "adr")
        if args.report:
            overview = parse_overview((root / "docs" / "adr-status.md").read_text())
            report(gh, steps, overview, args.today, args.post, args.dry_run)
        else:
            sync(gh, steps, args.dry_run)
    except ParseError as error:
        print(f"::error::{error}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
