"""Maintenance lines ``release/X.Y``: the rules a line follows (ADR 0116 point 3).

A line is cut from the newest release of its minor (``vX.Y.0``, or a later patch released
from ``main`` before a ``feat`` landed) only when a fix must ship and ``main`` holds a ``feat``
since that tag; otherwise the patch comes from ``main`` as before. A line takes fixes only: its
candidates and releases are patches ``X.Y.Z`` (Z > 0), never a ``feat`` and never a migration
(ADR 0116 point 4). A fix merges to ``main`` first; the label ``backport:X.Y`` opens the
cherry-pick PR on the line (``.github/workflows/backport.yml``).

Used by ``scripts/release_plan.py`` (a line's candidate), ``scripts/release_pr.py`` (the
release PR and tag on a line, and the line's changelog merged back into ``main``), ``main.yml``
(``tag-allowed``) and ``release.yml`` (``cut``). Subcommands, for the workflows:

- ``candidate``: on a line's checkout, the candidate ``X.Y.Z-rc.N`` a push to the line becomes
  (``main.yml``; ``scripts/release_plan.py --candidate`` does this for ``main``).
- ``cut --line X.Y``: whether the line may be cut now, and from which tag; prints ``tag=``,
  ``branch=``, ``label=``.
- ``tag-allowed --version X.Y.Z --commit SHA``: whether a release tag may sit on that commit
  (on ``main``, or a patch on its own line).
- ``backport-lines LABEL...``: the lines named by ``backport:X.Y`` labels, one per line.

Nothing here pushes, tags or opens anything.
"""

import argparse
import re
import subprocess  # noqa: S404 - runs git with fixed arguments only
import sys
from collections.abc import Iterable, Sequence

from scripts.release_plan import (
    candidate_version,
    git,
    last_release,
    messages_since,
    migrations_changed_since,
    next_version,
    superseded_by,
)

LINE_BRANCH = re.compile(r"^release/(\d+)\.(\d+)$")
"""A maintenance line's branch. A release PR's branch is ``release/vX.Y.Z`` and never matches."""
BACKPORT_LABEL = re.compile(r"^backport:(\d+\.\d+)$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SECTION = re.compile(r"^## (\d+\.\d+\.\d+) \([^)]*\)\n", re.M)


class LineError(ValueError):
    """A line rule refuses what was asked; the message says which rule."""


def line_of(branch: str) -> str | None:
    """Return ``X.Y`` for the branch ``release/X.Y`` (``refs/heads/`` allowed), else ``None``."""
    found = LINE_BRANCH.match(branch.removeprefix("refs/heads/"))
    return f"{found[1]}.{found[2]}" if found else None


def line_branch(line: str) -> str:
    """Return the branch of line ``X.Y``."""
    return f"release/{line}"


def _parts(version: str) -> tuple[int, int, int]:
    """Return ``X.Y.Z`` as numbers."""
    found = _VERSION.match(version)
    if not found:
        raise LineError(f"{version!r} is not a version X.Y.Z")
    return int(found[1]), int(found[2]), int(found[3])


def belongs_to(version: str, line: str) -> bool:
    """Return whether ``version`` is a patch of ``line`` (``X.Y.Z`` with Z > 0)."""
    major, minor, patch = _parts(version)
    return f"{major}.{minor}" == line and patch > 0


def candidate_base(
    line: str, last: str, messages: Sequence[str], migrations_changed: bool
) -> str | None:
    """Return the patch a line's next candidate or release is of; ``None`` with no commits.

    Args:
        line: The line, ``X.Y``.
        last: The last release on the line, ``X.Y.Z``.
        messages: Commit messages on the line since that release.
        migrations_changed: Whether ``migrations/versions/`` changed since that release.

    Raises:
        LineError: ``last`` is not of this line, a migration changed, or a commit is a
            ``feat`` or a breaking change (both raise MINOR, which a line never makes).
    """
    if not last.startswith(f"{line}."):
        raise LineError(f"the last release {last} is not on line {line}")
    if migrations_changed:
        raise LineError(f"a migration changed on line {line}; a patch carries none")
    proposed = next_version(last, messages)
    if proposed is None:
        return None
    if not belongs_to(proposed, line):
        raise LineError(f"line {line} takes fixes only, but its commits call for {proposed}")
    return proposed


def newest_release(line: str, tags: Iterable[str]) -> str | None:
    """Return the newest release tag ``vX.Y.Z`` of ``line`` among ``tags`` (no candidates).

    A line starts there: patches released from ``main`` before a ``feat`` landed (``v0.5.1``
    after ``v0.5.0``) are part of it, and its next patch must come after them.
    """
    patches = [
        (int(found[1]), tag.strip())
        for tag in tags
        if (found := re.fullmatch(rf"v{re.escape(line)}\.(\d+)", tag.strip()))
    ]
    return max(patches)[1] if patches else None


def may_cut(main_messages_since_tag: Sequence[str], tag: str) -> bool:
    """Return whether ``main`` holds a ``feat`` (or a breaking change) since ``tag`` (vX.Y.Z).

    Without one, the next patch comes from ``main`` and no line is needed.
    """
    current = tag.removeprefix("v")
    proposed = next_version(current, main_messages_since_tag)
    return proposed is not None and _parts(proposed)[2] == 0


def tag_allowed(version: str, on_main: bool, on_line: bool) -> bool:
    """Return whether a release tag ``vX.Y.Z`` may sit on a commit (ADR 0052 point 4).

    Args:
        version: The release, ``X.Y.Z``.
        on_main: The commit is on ``main``.
        on_line: The commit is on the line ``release/X.Y`` of the version.
    """
    _parts(version)
    return on_main or (on_line and _parts(version)[2] > 0)


def backport_lines(labels: Iterable[str]) -> list[str]:
    """Return the lines that ``backport:X.Y`` labels name, sorted, without duplicates."""
    return sorted(
        {found[1] for label in labels if (found := BACKPORT_LABEL.match(label.strip()))},
        key=lambda line: tuple(int(part) for part in line.split(".")),
    )


def merge_changelogs(text: str, others: Iterable[str]) -> str:
    """Return ``CHANGELOG.md`` with the released sections of ``others`` it lacks, in order.

    A line's patch releases have their own sections in the line's ``CHANGELOG.md``; the next
    release PR on ``main`` takes them over (ADR 0116 point 3). Sections are ordered by version,
    newest first, under ``## Unreleased``; a section ``main`` already has is left as it is.
    """
    head, sections = _split(text)
    for other in others:
        for version, body in _split(other)[1].items():
            sections.setdefault(version, body)
    ordered = sorted(sections, key=_parts, reverse=True)
    return (head + "".join(sections[version] for version in ordered)).rstrip("\n") + "\n"


def _split(text: str) -> tuple[str, dict[str, str]]:
    """Return the text before the first released section, and the sections by version."""
    matches = list(_SECTION.finditer(text))
    if not matches:
        return text, {}
    head = text[: matches[0].start()]
    bounds = [m.start() for m in matches] + [len(text)]
    sections = {
        match[1]: _ending(text[bounds[i] : bounds[i + 1]]) for i, match in enumerate(matches)
    }
    return head, sections


def _ending(section: str) -> str:
    """Return a section with exactly one blank line after it."""
    return section.rstrip("\n") + "\n\n"


def _is_ancestor(commit: str, ref: str) -> bool:
    """Return whether ``commit`` is in the history of ``ref``."""
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "merge-base", "--is-ancestor", commit, ref],  # noqa: S607 - git from PATH
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _ref_exists(ref: str) -> bool:
    """Return whether ``ref`` names a commit in this clone."""
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],  # noqa: S607
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _candidate(args: argparse.Namespace) -> None:
    line = line_of(args.branch)
    if line is None:
        raise LineError(f"{args.branch} is not a maintenance line release/X.Y")
    last = last_release()
    base = candidate_base(
        line, last.removeprefix("v"), messages_since(last), migrations_changed_since(last)
    )
    if base is None:
        sys.stderr.write(f"nothing merged on {args.branch} since {last}; no candidate\n")
        return
    later = superseded_by(git("tag", "--list", "v*-rc.*", "--contains", "HEAD").split())
    if later is not None:
        sys.stderr.write(f"HEAD is already in {later}; no candidate out of commit order\n")
        return
    sys.stdout.write(candidate_version(base, git("tag", "--list", f"v{base}-rc.*").split()) + "\n")


def _cut(args: argparse.Namespace) -> None:
    line = args.line
    if not re.fullmatch(r"\d+\.\d+", line):
        raise LineError(f"{line!r} is not a line X.Y")
    tag = newest_release(line, git("tag", "--list", f"v{line}.*").split())
    if tag is None:
        raise LineError(f"there is no release v{line}.0 to cut line {line} from")
    if _ref_exists(f"origin/{line_branch(line)}"):
        raise LineError(f"line {line} exists already")
    log = git("log", f"{tag}..origin/main", "--format=%B%x00", "--first-parent")
    messages = [message.strip() for message in log.split("\0") if message.strip()]
    if not may_cut(messages, tag):
        raise LineError(
            f"main holds no feat since {tag}; the next patch comes from main, no line is needed"
        )
    sys.stdout.write(f"tag={tag}\nbranch={line_branch(line)}\nlabel=backport:{line}\n")


def _tag_allowed(args: argparse.Namespace) -> None:
    major, minor, _ = _parts(args.version)
    line_ref = f"origin/{line_branch(f'{major}.{minor}')}"
    on_main = _is_ancestor(args.commit, "origin/main")
    on_line = _ref_exists(line_ref) and _is_ancestor(args.commit, line_ref)
    if not tag_allowed(args.version, on_main, on_line):
        raise LineError(
            f"v{args.version} is not on main and not a patch on {line_branch(f'{major}.{minor}')}"
        )
    where = "main" if on_main else line_branch(f"{major}.{minor}")
    sys.stdout.write(f"v{args.version} is on {where}\n")


def _backport_lines(args: argparse.Namespace) -> None:
    for line in backport_lines(args.labels):
        sys.stdout.write(f"{line}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print the reason and return 1 when a line rule refuses."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("candidate", help="the candidate a push to a line becomes")
    candidate.add_argument("--branch", required=True, help="the line's branch, release/X.Y")
    cut = commands.add_parser("cut", help="may line X.Y be cut now")
    cut.add_argument("--line", required=True)
    allowed = commands.add_parser("tag-allowed", help="may a release tag sit on this commit")
    allowed.add_argument("--version", required=True)
    allowed.add_argument("--commit", required=True)
    lines = commands.add_parser("backport-lines", help="the lines of backport:X.Y labels")
    lines.add_argument("labels", nargs="*")
    args = parser.parse_args(argv)
    try:
        handlers = {
            "candidate": _candidate,
            "cut": _cut,
            "tag-allowed": _tag_allowed,
            "backport-lines": _backport_lines,
        }
        handlers[args.command](args)
    except LineError as error:
        sys.stderr.write(f"::error::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
