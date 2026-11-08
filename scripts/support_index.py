"""Write ``support.json``, the support status of every minor's line (ADR 0116 point 2 and 9).

The newest minor is ``supported``. From 1.0.0 the one before it receives security fixes
(``security``, the window ``N-1`` of ``SECURITY.md``); before 1.0.0 only the newest minor is
supported. Every other minor has ``ended``, unless ``support.json`` already lists it as an
``extended`` line with an ``until`` date (a paid extended line): such entries are kept as they
are, and an extended line past its date has ended.

The release PR runs this (``scripts/release_pr.py prepare``) with the version it releases, so
the file on ``main`` is the current index; the worker reads it there (``GitHubReleases.support``)
and every release carries a signed copy. By hand::

    uv run python -m scripts.support_index [--add X.Y.Z] [--today YYYY-MM-DD]
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.release_plan import git, parse_version

SUPPORT_FILE = Path("support.json")
POLICY = "N-1 from 1.0.0, latest only before (SECURITY.md, ADR 0116 point 2)"
_STABLE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def index(released: Iterable[str], existing: dict[str, Any] | None, today: date) -> dict[str, Any]:
    """Return the support index for the released versions.

    Args:
        released: Release versions (``X.Y.Z`` or tags ``vX.Y.Z``); candidates are ignored.
        existing: The current ``support.json``, for its extended lines; ``None`` for none.
        today: The date that decides whether an extended line has ended.
    """
    minors = sorted(
        {
            parse_version(found[1])[:2]
            for version in released
            if (found := _STABLE.fullmatch(version.strip()))
        },
        reverse=True,
    )
    extended = {
        entry["line"]: entry
        for entry in (existing or {}).get("lines", [])
        if isinstance(entry, dict) and entry.get("status") == "extended" and entry.get("until")
    }
    lines = []
    for position, (major, minor) in enumerate(minors):
        line = f"{major}.{minor}"
        if position == 0:
            status, until = "supported", None
        elif position == 1 and minors[0][0] >= 1:
            status, until = "security", None
        elif line in extended and date.fromisoformat(extended[line]["until"]) >= today:
            status, until = "extended", extended[line]["until"]
        else:
            status, until = "ended", None
        lines.append({"line": line, "status": status, "until": until})
    return {"policy": POLICY, "lines": lines}


def write(path: Path, released: Sequence[str], today: date) -> None:
    """Write ``path`` from the released versions, keeping its extended lines."""
    existing = json.loads(path.read_text()) if path.exists() else None
    path.write_text(json.dumps(index(released, existing, today), indent=2) + "\n")


def released_tags() -> list[str]:
    """Return the release tags of this clone (no candidates)."""
    return [tag for tag in git("tag", "--list", "v*.*.*").split() if _STABLE.fullmatch(tag)]


def main(argv: Sequence[str] | None = None) -> int:
    """Write ``support.json`` from the tags, plus ``--add``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--add", default="", help="a version being released (X.Y.Z)")
    parser.add_argument("--today", default="", help="YYYY-MM-DD; default today (UTC)")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else datetime.now(UTC).date()
    write(SUPPORT_FILE, [*released_tags(), *([args.add] if args.add else [])], today)
    sys.stdout.write(f"{SUPPORT_FILE} written\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
