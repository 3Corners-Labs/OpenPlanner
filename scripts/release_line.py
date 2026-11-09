"""Maintenance lines ``release/X.Y``: cutting one, tagging on one, backporting to one (ADR 0116).

A line is cut from the newest release of its minor (``vX.Y.0``, or a later patch released
from ``main`` before a ``feat`` landed) only when a fix must ship and ``main`` holds a ``feat``
since that release; otherwise the patch comes from ``main`` as before. What a line admits (a
patch, never a ``feat`` or a migration) is ``scripts.release_plan.Target``; candidates on a
line come from ``scripts/release_plan.py --candidate --branch release/X.Y`` and the release PR
and tag from ``scripts/release_pr.py``. A fix merges to ``main`` first; the label
``backport:X.Y`` opens the cherry-pick PR on the line (``.github/workflows/backport.yml``), which
starts from the merge's push to ``main`` or from a dispatch, never from an event of a pull
request (ADR 0120).

Subcommands, for the workflows:

- ``cut --line X.Y``: whether the line may be cut now, and from which tag; prints ``tag=``,
  ``branch=``, ``label=`` (``release.yml``, step ``cut-line``).
- ``tag-allowed --version X.Y.Z --commit SHA``: whether a release tag may sit on that commit
  (on ``main``, or a patch on its own line); prints ``where=`` and ``latest=`` (``main.yml``).
- ``backports --repository OWNER/REPO --pull N... [--pushed SHA...]``: what to cherry-pick for
  those pull requests (the ones a push merged, listed by ``decision_gate.py
  --list-pull-requests``, with the push's commits; or the one dispatched); prints one JSON list
  of ``{number, commit, title, line}``, one entry per pull request and line, the matrix of
  ``backport.yml``'s job ``backport``.

Nothing here pushes, tags or opens anything; ``backports`` only reads pull requests.
"""

import argparse
import json
import re
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from scripts.release_plan import (
    MAIN,
    LineError,
    Target,
    git,
    git_ok,
    messages_since,
    next_version,
    parse_version,
)
from scripts.report_issue import Runner, run_gh

BACKPORT_LABEL = re.compile(r"^backport:(\d+\.\d+)$")


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
    proposed = next_version(tag.removeprefix("v"), main_messages_since_tag)
    return proposed is not None and parse_version(proposed)[2] == 0


def tag_target(version: str, on_main: bool, on_line: bool) -> Target | None:
    """Return where a release tag ``vX.Y.Z`` may sit (ADR 0052 point 4), or ``None``.

    Args:
        version: The release, ``X.Y.Z``.
        on_main: The commit is on ``main``.
        on_line: The commit is on the line ``release/X.Y`` of the version.
    """
    major, minor, _ = parse_version(version)
    line = Target(f"{major}.{minor}")
    if on_main:
        return Target()
    return line if on_line and line.admits(version) else None


def backport_lines(labels: Iterable[str]) -> list[str]:
    """Return the lines that ``backport:X.Y`` labels name, sorted, without duplicates."""
    return sorted(
        {found[1] for label in labels if (found := BACKPORT_LABEL.match(label.strip()))},
        key=lambda line: tuple(int(part) for part in line.split(".")),
    )


@dataclass(frozen=True)
class Backport:
    """One cherry-pick of a merged pull request onto one line (ADR 0116 point 3, ADR 0120)."""

    number: int
    commit: str
    """The pull request's merge commit on ``main``: git data that is cherry-picked, never run."""
    title: str
    line: str
    """``X.Y``, from a label that matches ``BACKPORT_LABEL``."""


def refusal(pull: Mapping[str, Any], pushed: Collection[str] | None) -> str | None:
    """Return why a listed pull request cannot be backported at all, or ``None``.

    Args:
        pull: The pull request as ``GET /repos/{repo}/pulls/{number}`` returns it.
        pushed: The commits of the push that listed it; ``None`` for a dispatch.

    A pull request must be merged into ``main``, and on a push its merge commit must be one of
    the pushed commits: a subject such as ``Merge pull request #12 ... (#34)`` names the wrong
    one, and GitHub may not mark a pull request merged at once. Either is a stop, never a quiet
    skip: the run fails and is re-run or dispatched (ADR 0120).
    """
    if not pull.get("merged") or (pull.get("base") or {}).get("ref") != MAIN.branch:
        return "it is not merged into main (yet)"
    if pushed is not None and pull.get("merge_commit_sha") not in pushed:
        return "its merge commit is none of the commits this push brought to main"
    return None


def backports_of(pull: Mapping[str, Any]) -> list[Backport]:
    """Return the cherry-picks of one merged pull request, one per line its labels name.

    A label such as ``backport:x`` names no line; no line label gives an empty list.
    """
    return [
        Backport(int(pull["number"]), str(pull["merge_commit_sha"]), str(pull["title"]), line)
        for line in backport_lines(str(label.get("name", "")) for label in pull.get("labels") or [])
    ]


def backports(
    numbers: Iterable[int],
    repository: str,
    run: Runner = run_gh,
    pushed: Collection[str] | None = None,
) -> list[Backport]:
    """Read each pull request once and return its cherry-picks, by number and line.

    Raises:
        LineError: A pull request has a ``refusal``; the message names it and why.
    """
    found = []
    for number in sorted(set(numbers)):
        pull = json.loads(run(["api", f"repos/{repository}/pulls/{number}"]))
        why = refusal(pull, pushed)
        if why is not None:
            raise LineError(f"#{number} cannot be backported: {why}")
        entries = backports_of(pull)
        sys.stderr.write(f"#{number}: {', '.join(e.line for e in entries) or 'no line label'}\n")
        found.extend(entries)
    return found


def _pull_number(value: str) -> int:
    """A pull request number from the dispatch: digits only."""
    if not (value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError(f"not a pull request number: {value!r}")
    return int(value)


def _cut(args: argparse.Namespace) -> None:
    target = Target.from_ref(f"release/{args.line}")
    if target.line is None:
        raise LineError(f"{args.line!r} is not a line X.Y")
    tag = newest_release(target.line, git("tag", "--list", f"v{target.line}.*").split())
    if tag is None:
        raise LineError(f"there is no release v{target.line}.0 to cut line {target.line} from")
    if git_ok("rev-parse", "--verify", "--quiet", f"origin/{target.branch}"):
        raise LineError(f"line {target.line} exists already")
    if not may_cut(messages_since(tag, "origin/main"), tag):
        raise LineError(
            f"main holds no feat since {tag}; the next patch comes from main, no line is needed"
        )
    sys.stdout.write(f"tag={tag}\nbranch={target.branch}\nlabel=backport:{target.line}\n")


def _tag_allowed(args: argparse.Namespace) -> None:
    major, minor, _ = parse_version(args.version)
    line_ref = f"origin/{Target(f'{major}.{minor}').branch}"
    on_main = git_ok("merge-base", "--is-ancestor", args.commit, "origin/main")
    on_line = git_ok("rev-parse", "--verify", "--quiet", line_ref) and git_ok(
        "merge-base", "--is-ancestor", args.commit, line_ref
    )
    target = tag_target(args.version, on_main, on_line)
    if target is None:
        raise LineError(f"v{args.version} is not on main and not a patch on {line_ref}")
    # A patch from a line is never GitHub's "latest" release or the image's :latest: a newer
    # minor may exist already (v0.5.2 after v0.6.0).
    latest = "true" if target.line is None else "false"
    sys.stdout.write(f"where={target.branch}\nlatest={latest}\n")


def main(argv: Sequence[str] | None = None, run: Runner = run_gh) -> int:
    """Run one subcommand; print the reason and return 1 when a line rule refuses."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cut = commands.add_parser("cut", help="may line X.Y be cut now")
    cut.add_argument("--line", required=True)
    allowed = commands.add_parser("tag-allowed", help="may a release tag sit on this commit")
    allowed.add_argument("--version", required=True)
    allowed.add_argument("--commit", required=True)
    found = commands.add_parser("backports", help="the cherry-picks of merged pull requests")
    found.add_argument("--repository", required=True, help="OWNER/REPO")
    found.add_argument("--pull", type=_pull_number, nargs="+", required=True, help="numbers")
    found.add_argument("--pushed", nargs="+", help="the push's commits (not for a dispatch)")
    args = parser.parse_args(argv)

    def _backports(args: argparse.Namespace) -> None:
        pushed = None if args.pushed is None else set(args.pushed)
        found = backports(args.pull, args.repository, run, pushed)
        sys.stdout.write(json.dumps([asdict(item) for item in found]) + "\n")

    handlers = {"cut": _cut, "tag-allowed": _tag_allowed, "backports": _backports}
    try:
        handlers[args.command](args)
    except ValueError as error:
        sys.stderr.write(f"::error::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
