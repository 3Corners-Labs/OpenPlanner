"""Propose the next release from the Conventional Commits since the last tag (ADR 0052).

Run ``make release-plan`` on an up-to-date ``main``. It prints the last release, the commits
since then and the version the rules of ADR 0052 give, so the product owner decides on a
release with the list in front of them. It changes nothing: no file, no tag, no push.

With ``--candidate [--branch REF]`` it prints only the release candidate a merge to ``main`` (or
to a maintenance line ``release/X.Y``, ADR 0116 point 3) becomes (ADR 0059): ``X.Y.Z-rc.N``,
where ``X.Y.Z`` is the next release of that branch's ``Target`` and ``N`` one above the highest
``rc`` tag of that version. ``main.yml`` tags and publishes it; release candidates never count
as the last release here. A candidate's number follows main's commit order: when an existing
candidate already contains ``HEAD`` (a run that started late, on an older merge than one already
tagged), it prints nothing and says on stderr which candidate superseded it, so ``main.yml``
tags nothing and an installation on the ``rc`` channel never goes back.
"""

import argparse
import re
import subprocess  # noqa: S404 - runs git with fixed arguments only
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_BREAKING = re.compile(r"^[a-z]+(\([^)]*\))?!:|^BREAKING[ -]CHANGE:", re.MULTILINE)
_FEATURE = re.compile(r"^feat(\([^)]*\))?:")
_CANDIDATE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)-rc\.(\d+)$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_LINE_BRANCH = re.compile(r"^release/(\d+)\.(\d+)$")


class LineError(ValueError):
    """A maintenance line's rule refuses what was asked; the message says which rule."""


def parse_version(version: str) -> tuple[int, int, int]:
    """Return ``X.Y.Z`` as numbers, or raise ``ValueError`` when it is not a version."""
    found = _VERSION.match(version)
    if not found:
        raise ValueError(f"{version!r} is not a version X.Y.Z")
    return int(found[1]), int(found[2]), int(found[3])


@dataclass(frozen=True)
class Target:
    """Where a release is made: ``main``, or a maintenance line ``release/X.Y`` (ADR 0116).

    Parsed once from the branch; every decision that differs between the two asks it. A line
    takes fixes only: its candidates and releases are patches ``X.Y.Z`` (Z > 0), never a
    ``feat`` and never a migration (ADR 0116 point 4).

    Attributes:
        line: ``X.Y`` for a line, ``None`` for ``main``.
    """

    line: str | None = None

    @classmethod
    def from_ref(cls, ref: str) -> "Target":
        """Return the target of a branch (``release/X.Y``, ``origin/`` or ``refs/heads/`` allowed).

        Any other branch is ``main``'s target: a dry run from a feature branch plans as
        ``main`` does. A release PR's own branch ``release/vX.Y.Z`` is no line.
        """
        name = ref.removeprefix("refs/heads/").removeprefix("origin/")
        found = _LINE_BRANCH.match(name)
        return cls(f"{found[1]}.{found[2]}" if found else None)

    @property
    def branch(self) -> str:
        """The branch on ``origin`` that releases of this target merge into."""
        return f"release/{self.line}" if self.line else "main"

    def admits(self, version: str) -> bool:
        """Whether ``version`` may be released here: anything on ``main``, a patch on a line."""
        major, minor, patch = parse_version(version)
        return self.line is None or (f"{major}.{minor}" == self.line and patch > 0)

    def next_version(
        self, current: str, messages: Sequence[str], migrations_changed: bool = False
    ) -> str | None:
        """Return the release after ``current`` that ``messages`` call for here.

        Raises:
            LineError: On a line, ``current`` is of another minor, a migration changed, or a
                commit is a ``feat`` or a breaking change (both raise MINOR).
        """
        if self.line is None:
            return next_version(current, messages, migrations_changed)
        if not current.startswith(f"{self.line}."):
            raise LineError(f"the last release {current} is not on line {self.line}")
        if migrations_changed:
            raise LineError(f"a migration changed on line {self.line}; a patch carries none")
        proposed = next_version(current, messages)
        if proposed is not None and not self.admits(proposed):
            raise LineError(
                f"line {self.line} takes fixes only, but its commits call for {proposed}"
            )
        return proposed

    def candidate_base(
        self, current: str, project: str, messages: Sequence[str], migrations_changed: bool
    ) -> str | None:
        """Return the release a candidate here is a candidate of (ADR 0059 point 1).

        One rule for both targets (``candidate_base``: a release PR's raised ``pyproject.toml``
        wins), and on a line the result must be a patch of the line.

        Raises:
            LineError: As ``next_version``, for a candidate a line does not admit.
        """
        if self.line is not None:
            self.next_version(current, [], migrations_changed)  # the line and migration rules
        base = candidate_base(current, project, messages, migrations_changed)
        if base is not None and not self.admits(base):
            raise LineError(f"line {self.line} takes fixes only, but its commits call for {base}")
        return base


MAIN = Target()
"""The target of ``main``."""


def next_version(
    current: str, messages: Sequence[str], migrations_changed: bool = False
) -> str | None:
    """Return the version after ``current`` that ``messages`` call for.

    Args:
        current: The last released version, ``MAJOR.MINOR.PATCH``.
        messages: Full commit messages (subject and body) since that release.
        migrations_changed: Whether ``migrations/versions/`` changed since that release.

    Returns:
        A breaking change raises MAJOR (MINOR before 1.0), a ``feat`` or a changed migration
        raises MINOR (a patch carries no migration, ADR 0116 point 4), anything else raises
        PATCH; ``None`` when there are no commits.
    """
    if not messages:
        return None
    major, minor, patch = (int(part) for part in current.split("."))
    breaking = any(_BREAKING.search(message) for message in messages)
    feature = any(_FEATURE.match(message) for message in messages)
    if breaking and major >= 1:
        return f"{major + 1}.0.0"
    if breaking or feature or migrations_changed:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def project_version(path: Path) -> str:
    """Return ``project.version`` from a ``pyproject.toml``."""
    version: str = tomllib.loads(path.read_text())["project"]["version"]
    return version


def candidate_base(
    current: str, project: str, messages: Sequence[str], migrations_changed: bool = False
) -> str | None:
    """Return the release a candidate on ``main`` is a candidate of (ADR 0059 point 1).

    Args:
        current: The last released version.
        project: ``project.version`` in ``pyproject.toml`` on ``main``.
        messages: Commit messages since the last release.
        migrations_changed: Whether ``migrations/versions/`` changed since the last release.

    Returns:
        ``project`` when a release PR already raised it above ``current`` (the product owner
        may choose a higher number than the rules give), else the version the rules give;
        ``None`` when nothing was merged since the last release.
    """
    if parse_version(project) > parse_version(current):
        return project
    return next_version(current, messages, migrations_changed)


def candidate_version(base: str, tags: Sequence[str]) -> str:
    """Return ``base-rc.N`` with ``N`` one above the highest ``rc`` tag of ``base`` in ``tags``."""
    numbers = [
        int(match.group(2))
        for tag in tags
        if (match := _CANDIDATE_TAG.fullmatch(tag.strip())) and match.group(1) == base
    ]
    return f"{base}-rc.{max(numbers, default=0) + 1}"


def superseded_by(containing: Sequence[str]) -> str | None:
    """Return the lowest candidate tag among ``containing``, or ``None`` when there is none.

    Args:
        containing: The tags whose commit has ``HEAD`` in its history
            (``git tag --contains HEAD``).

    Returns:
        The first candidate made from ``HEAD`` or a later merge; a commit it contains must not
        become a candidate with a higher number, or installations on the ``rc`` channel take
        the older code for newer (ADR 0059).
    """
    candidates = [
        (parse_version(match.group(1)), int(match.group(2)), tag.strip())
        for tag in containing
        if (match := _CANDIDATE_TAG.fullmatch(tag.strip()))
    ]
    return min(candidates)[2] if candidates else None


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


def git_ok(*args: str) -> bool:
    """Return whether one git command succeeds (``merge-base --is-ancestor``, ``rev-parse``)."""
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", *args],  # noqa: S607 - git from PATH, as every developer runs it
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def last_release() -> str:
    """Return the tag of the last release reachable from ``HEAD``, for example ``v0.3.0``.

    Release candidates are tags too (ADR 0059); the last *release* is the last tag without
    ``-rc``.
    """
    return git(
        "describe", "--tags", "--abbrev=0", "--match", "v*.*.*", "--exclude", "v*-rc.*"
    ).strip()


def messages_since(ref: str, head: str = "HEAD") -> list[str]:
    """Return the full commit messages on the first-parent line from ``ref`` to ``head``."""
    log = git("log", f"{ref}..{head}", "--format=%B%x00", "--first-parent")
    return [message.strip() for message in log.split("\0") if message.strip()]


def migrations_changed_since(ref: str) -> bool:
    """Return whether a migration file was added, changed or removed from ``ref`` to ``HEAD``."""
    return bool(git("diff", "--name-only", f"{ref}..HEAD", "--", "migrations/versions").strip())


def main(argv: Sequence[str] | None = None) -> int:
    """Print the release proposal for the current checkout, or its release candidate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate", action="store_true", help="print only the X.Y.Z-rc.N a merge becomes"
    )
    parser.add_argument(
        "--branch", default="main", help="the branch merged into: main or a line release/X.Y"
    )
    args = parser.parse_args(argv)
    target = Target.from_ref(args.branch)
    last = last_release()
    messages = messages_since(last)
    project = project_version(Path("pyproject.toml"))
    migrations = migrations_changed_since(last)
    if args.candidate:
        try:
            base = target.candidate_base(last.removeprefix("v"), project, messages, migrations)
        except LineError as error:
            sys.stderr.write(f"::error::{error}\n")
            return 1
        if base is None:
            # Pushing a freshly cut line is such a case: nothing to tag, nothing failed.
            sys.stderr.write(f"nothing merged on {target.branch} since {last}; no candidate\n")
            return 0
        later = superseded_by(git("tag", "--list", "v*-rc.*", "--contains", "HEAD").split())
        if later is not None:
            sys.stderr.write(f"HEAD is already in {later}; no candidate out of commit order\n")
            return 0
        tags = git("tag", "--list", f"v{base}-rc.*").split()
        sys.stdout.write(candidate_version(base, tags) + "\n")
        return 0
    proposal = next_version(last.removeprefix("v"), messages, migrations)
    out = [f"last release   {last}", f"pyproject      {project}"]
    out += [
        f"commits since  {len(messages)}",
        f"migrations     {'changed' if migrations else 'unchanged'}",
    ]
    out += [f"  {message.splitlines()[0]}" for message in messages]
    out += [f"next version   {proposal or 'none: nothing to release'}"]
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
