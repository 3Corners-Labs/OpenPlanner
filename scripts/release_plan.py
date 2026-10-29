"""Propose the next release from the Conventional Commits since the last tag (ADR 0051).

Run ``make release-plan`` on an up-to-date ``main``. It prints the last release, the commits
since then and the version the rules of ADR 0051 give, so the product owner decides on a
release with the list in front of them. It changes nothing: no file, no tag, no push.
"""

import re
import subprocess  # noqa: S404 - runs git with fixed arguments only
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

_BREAKING = re.compile(r"^[a-z]+(\([^)]*\))?!:|^BREAKING[ -]CHANGE:", re.MULTILINE)
_FEATURE = re.compile(r"^feat(\([^)]*\))?:")


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


def _git(*args: str) -> str:
    """Return the output of one git command."""
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", *args],  # noqa: S607 - git from PATH, as every developer runs it
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main() -> int:
    """Print the release proposal for the current checkout."""
    last = _git("describe", "--tags", "--abbrev=0", "--match", "v*.*.*").strip()
    log = _git("log", f"{last}..HEAD", "--format=%B%x00", "--first-parent")
    messages = [message.strip() for message in log.split("\0") if message.strip()]
    proposal = next_version(last.removeprefix("v"), messages)
    out = [f"last release   {last}", f"pyproject      {project_version(Path('pyproject.toml'))}"]
    out += [f"commits since  {len(messages)}"]
    out += [f"  {message.splitlines()[0]}" for message in messages]
    out += [f"next version   {proposal or 'none: nothing to release'}"]
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
