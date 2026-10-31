"""Tell build work from a decision, so that only decisions wait for the product owner (ADR 0078).

``.github/workflows/decision-gate.yml`` runs this as the required check *decision gate* twice:
on every pull request, from ``main``'s copy of this file (``pull_request_target``), so a pull
request cannot change the gate that judges it; and again in the merge queue (``merge_group``,
ADR 0088), where it judges every pull request in the group by its own commit on the queue
branch, so that a decision cannot reach ``main`` through a stale or skipped check on the pull
request. A pull request is a *decision* when it touches an ADR
outside its *Build status* table and its Status line (a new or removed ADR always counts),
``CLAUDE.md``, ``SECURITY.md``, the authorisation matrix, anything under ``.github/`` or
``.semgrep/``, the ``Makefile`` (what CI runs), the gates themselves (this script and the
release audit scripts), or when its title starts with ``chore(release):``. A decision passes
the check only with the label ``po:approved`` on the pull request; build work passes without
it. The output names every file that made the pull request a decision, so the product owner
knows what they are approving.

The list of decision paths is fixed on purpose and lives here: changing it is itself a
decision (ADR 0078, *Settings*).

In the merge queue every first-parent commit between the group's base (``main``) and its head
is one pull request's squash commit, whose subject ends in ``(#N)``, or its merge commit,
whose subject starts with ``Merge pull request #N``. ``--list-pull-requests`` prints those
numbers so the workflow can ask the API for each pull request's labels; ``--merge-group`` then
judges each commit against its parent with the labels given as ``--pull N=label,label``. A
commit that names no pull request, or a pull request whose labels were not given, fails the
check: the gate fails closed.
"""

import argparse
import re
import subprocess  # noqa: S404 - runs git with fixed arguments only
import sys
from collections.abc import Callable, Iterable, Sequence

LABEL = "po:approved"
"""The product owner's approval; only a collaborator can set a label."""

DECISION_FILES = frozenset(
    {
        "CLAUDE.md",
        "SECURITY.md",
        "Makefile",
        "app/domain/authorization.py",
        "scripts/decision_gate.py",
        "scripts/release-audit.sh",
        "scripts/check-release-audit.sh",
    }
)
"""Files whose every change is a decision: the agreements, the matrix and the gates."""

DECISION_DIRECTORIES = (".github/", ".semgrep/")
"""Directories whose every change is a decision: workflows, templates and the repo's own rules."""

ADR_DIRECTORY = "docs/adr/"
RELEASE_TITLE = "chore(release):"

_PULL_REQUEST = re.compile(r"\(#(\d+)\)$|^Merge pull request #(\d+) ")
"""The pull request number in a squash commit's subject or a merge commit's subject."""

_STATUS = re.compile(r"^Status:.*?(?:\n\n|\Z)", re.M | re.S)
_BUILD_STATUS = re.compile(r"^## Build status\n.*?(?=^## |\Z)", re.M | re.S)

Texts = Callable[[str], tuple[str | None, str | None]]
"""Gives a path's text before and after the change; ``None`` where the file does not exist."""


def decision_part(adr: str) -> str:
    """Return an ADR's text without the parts that build work may change.

    Args:
        adr: The ADR's full text.

    Returns:
        The text without its Status paragraph and its *Build status* section (ADR 0059
        point 7): what remains is the decision itself.
    """
    return _BUILD_STATUS.sub("", _STATUS.sub("", adr, count=1), count=1)


def adr_changed_decision(before: str | None, after: str | None) -> bool:
    """Return whether a change to one ADR file touches its decision.

    Args:
        before: The ADR's text on the base, or ``None`` when the file is new.
        after: The ADR's text on the head, or ``None`` when the file is removed.

    Returns:
        ``True`` for a new or removed ADR, and for a change outside the Status paragraph and
        the *Build status* table.
    """
    if before is None or after is None:
        return True
    return decision_part(before) != decision_part(after)


def reasons(paths: Iterable[str], title: str, texts: Texts) -> list[str]:
    """Return why a pull request is a decision, one line per cause.

    Args:
        paths: Every path the pull request changes.
        title: The pull request's title.
        texts: How to read an ADR's text before and after the change.

    Returns:
        An empty list for build work.
    """
    found = []
    if title.startswith(RELEASE_TITLE):
        found.append("the title says it is a release")
    for path in sorted(paths):
        if path in DECISION_FILES or path.startswith(DECISION_DIRECTORIES):
            found.append(f"{path} changed")
        elif path.startswith(ADR_DIRECTORY) and adr_changed_decision(*texts(path)):
            found.append(f"{path} changed outside its Status line and Build status table")
    return found


def pull_request_number(subject: str) -> int | None:
    """Return the pull request a commit on the merge queue branch merges, or ``None``.

    Args:
        subject: The commit's subject line.

    Returns:
        The number from ``... (#N)`` (a squash commit) or ``Merge pull request #N ...`` (a
        merge commit); ``None`` when the subject names no pull request.
    """
    found = _PULL_REQUEST.search(subject.strip())
    if found is None:
        return None
    return int(found.group(1) or found.group(2))


def parse_pull(argument: str) -> tuple[int, set[str]]:
    """Parse one ``--pull N=label,label`` argument into the number and its labels."""
    number, _, labels = argument.partition("=")
    return int(number), {label.strip() for label in labels.split(",") if label.strip()}


def _git(*args: str) -> str:
    """Run git with fixed arguments and return its output."""
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", *args],  # noqa: S607 - git from PATH, as every developer runs it
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _show(revision: str, path: str) -> str | None:
    """Return a file's text at a revision, or ``None`` when it does not exist there."""
    try:
        return _git("show", f"{revision}:{path}")
    except subprocess.CalledProcessError:
        return None


def _judge(base: str, head: str, title: str, labels: set[str]) -> int:
    """Print what makes one change a decision and return 1 when it lacks the label."""
    paths = _git("diff", "--name-only", base, head).split()
    found = reasons(paths, title, lambda path: (_show(base, path), _show(head, path)))
    if not found:
        print("build work: merges on green (ADR 0078)")
        return 0
    print("decision (ADR 0078):")
    for reason in found:
        print(f"  - {reason}")
    if LABEL in labels:
        print(f"approved: the label {LABEL} is set")
        return 0
    print(f"::error::a decision waits for the product owner: the label {LABEL} is missing")
    return 1


def _queue_commits(base: str, head: str) -> list[tuple[str, str, int | None]]:
    """Return the merge queue's commits between ``base`` and ``head``, oldest first.

    Each item is the commit, its subject and the pull request it merges (``None`` when the
    subject names none).
    """
    start = _git("merge-base", base, head).strip()
    commits = _git("rev-list", "--first-parent", "--reverse", f"{start}..{head}").split()
    found = []
    for commit in commits:
        subject = _git("log", "-1", "--format=%s", commit).strip()
        found.append((commit, subject, pull_request_number(subject)))
    return found


def _list_pull_requests(base: str, head: str) -> int:
    """Print the pull requests in the merge group, one per line; 1 when a commit names none."""
    status = 0
    for commit, subject, number in _queue_commits(base, head):
        if number is None:
            print(f"::error::commit {commit[:12]} names no pull request: {subject}")
            status = 1
        else:
            print(number)
    return status


def _judge_merge_group(base: str, head: str, pulls: dict[int, set[str]]) -> int:
    """Judge every pull request in the merge group by its own commit; 1 when one fails."""
    status = 0
    for commit, subject, number in _queue_commits(base, head):
        print(f"{commit[:12]} {subject}")
        if number is None:
            print("::error::the commit names no pull request, so its labels cannot be read")
            status = 1
            continue
        if number not in pulls:
            print(f"::error::the labels of pull request #{number} were not given")
            status = 1
            continue
        status = max(status, _judge(f"{commit}^", commit, subject, pulls[number]))
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Print what makes the pull request a decision and exit 1 when it lacks the label."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="the base commit of the pull request")
    parser.add_argument("--head", required=True, help="the head commit of the pull request")
    parser.add_argument("--title", default="", help="the pull request's title")
    parser.add_argument("--labels", default="", help="the pull request's labels, comma-separated")
    parser.add_argument(
        "--list-pull-requests",
        action="store_true",
        help="print the pull requests merged by the commits between base and head, one per line",
    )
    parser.add_argument(
        "--merge-group",
        action="store_true",
        help="judge every pull request between base and head by its own commit (merge queue)",
    )
    parser.add_argument(
        "--pull",
        action="append",
        default=[],
        metavar="N=LABEL,LABEL",
        help="a pull request in the merge group with its labels (with --merge-group)",
    )
    args = parser.parse_args(argv)

    if args.list_pull_requests:
        return _list_pull_requests(args.base, args.head)
    if args.merge_group:
        return _judge_merge_group(args.base, args.head, dict(map(parse_pull, args.pull)))

    base = _git("merge-base", args.base, args.head).strip()
    labels = {label.strip() for label in args.labels.split(",")}
    return _judge(base, args.head, args.title, labels)


if __name__ == "__main__":
    sys.exit(main())
