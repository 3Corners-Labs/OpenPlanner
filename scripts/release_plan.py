"""Propose the next release from the Conventional Commits since the last tag (ADR 0052).

Run ``make release-plan`` on an up-to-date ``main``. It prints the last release, the commits
since then and the version the rules of ADR 0052 give, so the product owner decides on a
release with the list in front of them. It changes nothing: no file, no tag, no push.

With ``--candidate`` it prints only the release candidate a merge to ``main`` becomes
(ADR 0059): ``X.Y.Z-rc.N``, where ``X.Y.Z`` is the next release and ``N`` one above the highest
``rc`` tag of that version. ``main.yml`` tags and publishes it; release candidates never count
as the last release here.
"""

import argparse
import re
import subprocess  # noqa: S404 - runs git with fixed arguments only
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

_BREAKING = re.compile(r"^[a-z]+(\([^)]*\))?!:|^BREAKING[ -]CHANGE:", re.MULTILINE)
_FEATURE = re.compile(r"^feat(\([^)]*\))?:")
_CANDIDATE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)-rc\.(\d+)$")


def next_version(current: str, messages: Sequence[str]) -> str | None:
    """Return the version after ``current`` that ``messages`` call for.

    Args:
        current: The last released version, ``MAJOR.MINOR.PATCH``.
        messages: Full commit messages (subject and body) since that release.

    Returns:
        A breaking change raises MAJOR (MINOR before 1.0), a ``feat`` raises MINOR, anything
        else raises PATCH; ``None`` when there are no commits.
    """
    if not messages:
        return None
    major, minor, patch = (int(part) for part in current.split("."))
    breaking = any(_BREAKING.search(message) for message in messages)
    feature = any(_FEATURE.match(message) for message in messages)
    if breaking and major >= 1:
        return f"{major + 1}.0.0"
    if breaking or feature:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def project_version(path: Path) -> str:
    """Return ``project.version`` from a ``pyproject.toml``."""
    version: str = tomllib.loads(path.read_text())["project"]["version"]
    return version


def _numbers(version: str) -> tuple[int, ...]:
    """Return ``MAJOR.MINOR.PATCH`` as a tuple, for ordering."""
    return tuple(int(part) for part in version.split("."))


def candidate_base(current: str, project: str, messages: Sequence[str]) -> str | None:
    """Return the release a candidate on ``main`` is a candidate of (ADR 0059 point 1).

    Args:
        current: The last released version.
        project: ``project.version`` in ``pyproject.toml`` on ``main``.
        messages: Commit messages since the last release.

    Returns:
        ``project`` when a release PR already raised it above ``current`` (the product owner
        may choose a higher number than the rules give), else the version the rules give;
        ``None`` when nothing was merged since the last release.
    """
    if _numbers(project) > _numbers(current):
        return project
    return next_version(current, messages)


def candidate_version(base: str, tags: Sequence[str]) -> str:
    """Return ``base-rc.N`` with ``N`` one above the highest ``rc`` tag of ``base`` in ``tags``."""
    numbers = [
        int(match.group(2))
        for tag in tags
        if (match := _CANDIDATE_TAG.fullmatch(tag.strip())) and match.group(1) == base
    ]
    return f"{base}-rc.{max(numbers, default=0) + 1}"


def git(*args: str) -> str:
    """Return the output of one git command, run in the current directory.

    Shared with ``scripts/release_pr.py``; both only ever pass fixed arguments.
    """
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", *args],  # noqa: S607 - git from PATH, as every developer runs it
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def last_release() -> str:
    """Return the tag of the last release reachable from ``HEAD``, for example ``v0.3.0``.

    Release candidates are tags too (ADR 0059); the last *release* is the last tag without
    ``-rc``.
    """
    return git(
        "describe", "--tags", "--abbrev=0", "--match", "v*.*.*", "--exclude", "v*-rc.*"
    ).strip()


def messages_since(ref: str) -> list[str]:
    """Return the full commit messages on the first-parent line from ``ref`` to ``HEAD``."""
    log = git("log", f"{ref}..HEAD", "--format=%B%x00", "--first-parent")
    return [message.strip() for message in log.split("\0") if message.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    """Print the release proposal for the current checkout, or its release candidate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate", action="store_true", help="print only the X.Y.Z-rc.N a merge becomes"
    )
    args = parser.parse_args(argv)
    last = last_release()
    messages = messages_since(last)
    project = project_version(Path("pyproject.toml"))
    if args.candidate:
        base = candidate_base(last.removeprefix("v"), project, messages)
        if base is None:
            sys.stderr.write("nothing merged since the last release; no candidate\n")
            return 1
        tags = git("tag", "--list", f"v{base}-rc.*").split()
        sys.stdout.write(candidate_version(base, tags) + "\n")
        return 0
    proposal = next_version(last.removeprefix("v"), messages)
    out = [f"last release   {last}", f"pyproject      {project}"]
    out += [f"commits since  {len(messages)}"]
    out += [f"  {message.splitlines()[0]}" for message in messages]
    out += [f"next version   {proposal or 'none: nothing to release'}"]
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
