"""The bundled proxy's body limits leave room for configuration imports (ADR 0089, issue 504).

Caddy applies every ``request_body`` whose matcher fits and the smallest limit wins, so the test
works out the effective limit per path from the Caddyfile instead of looking for lines.
"""

import re
from fnmatch import fnmatchcase
from pathlib import Path

from app.config import AGENT_API_PREFIX, Settings

REPO = Path(__file__).parents[2]
CADDYFILE = (REPO / "deploy/proxy/Caddyfile").read_text()
UNITS = {"KB": 1000, "MB": 1000**2, "KiB": 1024, "MiB": 1024**2}
IMPORT_PATHS = ("/admin/settings/package/import", f"{AGENT_API_PREFIX}/configuration")


def _ceiling() -> int:
    """Return the largest package the settings allow, in bytes."""
    field = Settings.model_fields["configuration_package_max_mb"]
    upper = next(m.le for m in field.metadata if hasattr(m, "le"))
    return int(upper) * 1024 * 1024


def _matchers() -> dict[str, tuple[bool, list[str]]]:
    """Return each named path matcher as (negated, patterns)."""
    found = {}
    for name, negated, patterns in re.findall(r"^\t(@\w+) (not )?path ([^\n]+)$", CADDYFILE, re.M):
        found[name] = (bool(negated), patterns.split())
    return found


def _limit(path: str) -> int | None:
    """Return the effective body limit in bytes for ``path``: the smallest that matches."""
    matchers = _matchers()
    sizes = []
    for name, size, unit in re.findall(r"request_body (@\w+) \{\s*max_size (\d+)(\w+)", CADDYFILE):
        negated, patterns = matchers[name]
        hit = any(fnmatchcase(path, pattern) for pattern in patterns)
        if hit != negated:
            sizes.append(int(size) * UNITS[unit])
    return min(sizes) if sizes else None


def test_both_import_paths_take_a_package_of_the_settings_ceiling() -> None:
    for path in IMPORT_PATHS:
        limit = _limit(path)
        assert limit is not None
        assert limit >= _ceiling(), path


def test_other_paths_keep_the_small_limits() -> None:
    assert _limit("/g/demo") == 64 * 1000
    assert _limit("/b/token") == 64 * 1000
    assert _limit("/admin/settings/other") == 1000**2
    assert _limit(f"{AGENT_API_PREFIX}/bookings") == 1000**2
