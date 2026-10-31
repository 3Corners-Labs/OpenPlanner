"""Tell build work from a decision, so that only decisions wait for the product owner (ADR 0078).

``.github/workflows/decision-gate.yml`` runs this on every pull request as the required check
*decision gate*, from ``main``'s copy of this file (``pull_request_target``), so a pull request
cannot change the gate that judges it. A pull request is a *decision* when it touches an ADR
outside its *Build status* table and its Status line (a new or removed ADR always counts),
``CLAUDE.md``, ``SECURITY.md``, the authorisation matrix, anything under ``.github/`` or
``.semgrep/``, the ``Makefile`` (what CI runs), the gates themselves (this script and the
release audit scripts), or when its title starts with ``chore(release):``. A decision passes
the check only with the label ``po:approved`` on the pull request; build work passes without
it. The output names every file that made the pull request a decision, so the product owner
knows what they are approving.

The list of decision paths is fixed on purpose and lives here: changing it is itself a
decision (ADR 0078, *Settings*).
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


def main(argv: Sequence[str] | None = None) -> int:
    """Print what makes the pull request a decision and exit 1 when it lacks the label."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="the base commit of the pull request")
    parser.add_argument("--head", required=True, help="the head commit of the pull request")
    parser.add_argument("--title", default="", help="the pull request's title")
    parser.add_argument("--labels", default="", help="the pull request's labels, comma-separated")
    args = parser.parse_args(argv)

    base = _git("merge-base", args.base, args.head).strip()
    paths = _git("diff", "--name-only", base, args.head).split()
    found = reasons(paths, args.title, lambda path: (_show(base, path), _show(args.head, path)))
    labels = {label.strip() for label in args.labels.split(",")}

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


if __name__ == "__main__":
    sys.exit(main())
