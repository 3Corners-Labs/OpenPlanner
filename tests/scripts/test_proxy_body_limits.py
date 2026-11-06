"""The bundled proxy's body limits leave room for configuration imports (ADR 0089, issue 504)."""

import re
from pathlib import Path

REPO = Path(__file__).parents[2]
CADDYFILE = (REPO / "deploy/proxy/Caddyfile").read_text()
IMPORT_PATH = "/admin/settings/package/import"
CEILING_MB = 512  # upper bound of configuration_package_max_mb in app/config.py


def _limits() -> list[tuple[str, int]]:
    """Return (matcher, megabytes-or-kilobytes as bytes) of every request_body block."""
    found = []
    for matcher, size, unit in re.findall(
        r"request_body\s*(@\w+)?\s*\{\s*max_size\s+(\d+)(KB|MB)", CADDYFILE
    ):
        found.append((matcher, int(size) * (1024 if unit == "KB" else 1024 * 1024)))
    return found


def test_the_import_path_has_its_own_limit_at_the_settings_ceiling() -> None:
    matchers = re.findall(rf"(@\w+)\s+path\s+{re.escape(IMPORT_PATH)}\s*$", CADDYFILE, re.M)
    assert matchers, "the import path needs a named matcher"
    limits = dict(_limits())
    assert limits.get(matchers[0], 0) >= CEILING_MB * 1024 * 1024


def test_no_other_limit_applies_to_the_import_path() -> None:
    # Every smaller limit must exclude the import path, or it would still win.
    for matcher, size in _limits():
        if size >= CEILING_MB * 1024 * 1024:
            continue
        if matcher:
            assert re.search(rf"{matcher}\s+(not\s+path|path)", CADDYFILE)
        else:
            raise AssertionError("an unmatched small limit would cap the import path")
    assert re.search(r"not\s+path\s+[^\n]*" + re.escape(IMPORT_PATH), CADDYFILE)
