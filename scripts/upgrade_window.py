"""Choose the upgrade fixtures the matrix runs on (ADR 0116 point 7).

Reads the repository's releases as GitHub's API returns them (``gh api
repos/OWNER/REPO/releases``) on stdin and prints one release tag per line: the newest patch of
each of the newest ``WINDOW`` minors that carries an upgrade fixture, and the newest patch of
the stop (``--stop X.Y.Z``, the release's ``MIN_UPGRADE_FROM``) when it carries one. Release
candidates, drafts and releases without a fixture are skipped; a fixture counts only when the
release job uploaded it (``github-actions[bot]``) together with its ``.sha256`` file, so a
fixture attached by hand is never used (threat model: "a fixture with real personal data").

Prints nothing when no release carries a fixture yet; the matrix then has nothing to run.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from typing import Any

WINDOW = 2
"""The support window ``N-1`` of ADR 0116 point 2: the newest minor and the one before it."""
RELEASE_UPLOADER = "github-actions[bot]"
"""The account that uploads release assets from ``main.yml``."""
_STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def fixture_name(tag: str) -> str:
    """Return the file name of the upgrade fixture of release ``tag`` (``vX.Y.Z``)."""
    return f"upgrade-fixture-{tag.removeprefix('v')}.sqlite"


def _has_fixture(release: dict[str, Any]) -> bool:
    """Whether the release carries its fixture and checksum, both uploaded by the release job."""
    name = fixture_name(release["tag_name"])
    uploaded = {
        asset["name"]
        for asset in release.get("assets", [])
        if (asset.get("uploader") or {}).get("login") == RELEASE_UPLOADER
    }
    return name in uploaded and f"{name}.sha256" in uploaded


def _version(tag: str) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` of a stable release tag, ``None`` for anything else."""
    match = _STABLE.fullmatch(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def choose(releases: Iterable[dict[str, Any]], stop: str | None = None) -> list[str]:
    """Return the release tags whose fixtures the matrix runs on, newest first.

    Args:
        releases: Releases as GitHub's API returns them.
        stop: The minimum upgrade-from version (``X.Y.Z``); its minor is added to the window.
    """
    newest: dict[tuple[int, int], tuple[int, str]] = {}
    for release in releases:
        version = _version(release.get("tag_name", ""))
        if version is None or release.get("draft") or not _has_fixture(release):
            continue
        minor = version[:2]
        if minor not in newest or newest[minor][0] < version[2]:
            newest[minor] = (version[2], release["tag_name"])
    minors = sorted(newest, reverse=True)[:WINDOW]
    stop_version = _version(f"v{stop}") if stop else None
    if stop_version and stop_version[:2] in newest and stop_version[:2] not in minors:
        minors.append(stop_version[:2])
    return [newest[minor][1] for minor in minors]


def main(argv: Sequence[str] | None = None) -> int:
    """Read the releases from stdin and print the chosen tags."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stop", help="MIN_UPGRADE_FROM of the release under test (X.Y.Z)")
    args = parser.parse_args(argv)
    for tag in choose(json.load(sys.stdin), args.stop):
        sys.stdout.write(f"{tag}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
