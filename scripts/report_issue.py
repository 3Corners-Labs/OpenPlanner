"""One open GitHub issue that carries a report, created, updated in place and closed (ADR 0109).

Two checks report this way: the ADR drift report (``scripts/adr_issues.py``, ADR 0078 point 6)
and the guardrail audit (``scripts/guardrail_audit.py``, ADR 0109). Each keeps exactly one open
issue whose title starts with its own prefix and carries its own label: the report is written
into it when something is found, and the issue is closed with a comment when nothing is.
Everything that talks to GitHub goes through :class:`IssueTracker`, a thin layer over the ``gh``
CLI, so the logic is pure and tested with a fake.
"""

import json
import subprocess  # noqa: S404 - runs gh with fixed arguments only
from collections.abc import Callable, Sequence
from datetime import date

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


class IssueTracker:
    """Reads and writes issues and labels through ``gh``; subclasses add what they need."""

    colour = "5319e7"
    """The colour of the labels this tracker creates."""

    def __init__(self, run: Runner = run_gh) -> None:
        """Keep the runner; tests pass a fake."""
        self._run = run

    def labels(self) -> set[str]:
        """Every label of the repository."""
        rows = json.loads(self._run(["label", "list", "--json", "name", "--limit", "500"]))
        return {str(row["name"]) for row in rows}

    def open_issues(self) -> list[tuple[int, str]]:
        """The number and title of every open issue, oldest first."""
        rows = json.loads(
            self._run(
                ["issue", "list", "--state", "open", "--limit", "1000", "--json", "number,title"]
            )
        )
        return sorted((int(row["number"]), str(row["title"])) for row in rows)

    def create_label(self, name: str, description: str) -> None:
        """Create a label."""
        self._run(["label", "create", name, "--color", self.colour, "--description", description])

    def create_issue(self, title: str, body: str, label: str) -> int:
        """Create an issue and return its number (gh prints its URL)."""
        url = self._run(["issue", "create", "--title", title, "--body", body, "--label", label])
        return int(url.strip().rsplit("/", 1)[-1])

    def close_issue(self, number: int, comment: str) -> None:
        """Close an issue with a comment that says why."""
        self._run(["issue", "close", str(number), "--comment", comment])

    def edit_issue(self, number: int, title: str, body: str) -> None:
        """Replace an issue's title and body."""
        self._run(["issue", "edit", str(number), "--title", title, "--body", body])


def say(dry_run: bool, what: str) -> None:
    """Print what happens, or what would happen in a dry run."""
    print(f"would {what}" if dry_run else what)


def ensure_label(
    tracker: IssueTracker, labels: set[str], name: str, description: str, dry_run: bool
) -> None:
    """Create a label unless it exists; ``labels`` is updated so it is created once."""
    if name in labels:
        return
    say(dry_run, f"create label {name}")
    if not dry_run:
        tracker.create_label(name, description)
    labels.add(name)


def keep_report(
    tracker: IssueTracker,
    open_issues: Sequence[tuple[int, str]],
    prefix: str,
    label: str,
    description: str,
    text: str,
    found: bool,
    today: date,
    dry_run: bool,
) -> None:
    """Keep the one open issue ``<prefix> <date>`` in step with the report.

    Args:
        tracker: Where the issue lives.
        open_issues: The open issues as ``(number, title)``; the oldest with the prefix is ours.
        prefix: The start of the issue's title, for example ``Guardrail drift``.
        label: The label the issue gets when it is created.
        description: The label's description, when the label has to be created.
        text: The report, which becomes the issue's body.
        found: Whether the report found anything; without findings the issue is closed.
        today: The date in the title and in the closing comment.
        dry_run: Say what would change and write nothing.
    """
    title = f"{prefix} {today.isoformat()}"
    existing = next((number for number, name in open_issues if name.startswith(prefix)), None)
    if not found:
        if existing is not None:
            say(dry_run, f"close #{existing}: nothing drifts on {today.isoformat()}")
            if not dry_run:
                tracker.close_issue(
                    existing, f"Nothing drifts on {today.isoformat()}; closed by the check."
                )
        return
    if existing is not None:
        say(dry_run, f"update #{existing}: {title}")
        if not dry_run:
            tracker.edit_issue(existing, title, text)
        return
    ensure_label(tracker, tracker.labels(), label, description, dry_run)
    say(dry_run, f"create: {title}")
    if not dry_run:
        tracker.create_issue(title, text, label)
