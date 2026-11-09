"""``coveredYears`` of the role-manuals skill: the years a capture covers in its screenshots.

Rule 4 of ``redact.js`` covers the year of a capture, the one before and the five after it. The
window comes from the capture's recorded start, so the screenshots and a log rewritten later name
the same years, and a start without a year stops the capture instead of covering nothing. Runs
the module in node, as the capture does.
"""

import json
import shutil
import subprocess

import pytest

from tests.support.skill_scripts import SKILLS

NODE = shutil.which("node")
EVIDENCE = SKILLS / "role-manuals" / "scripts" / "evidence.mjs"

pytestmark = pytest.mark.skipif(NODE is None, reason="the capture scripts run in node")


def covered_years(started: object) -> subprocess.CompletedProcess[str]:
    """Run ``coveredYears(started)`` in node; its JSON result is on stdout."""
    script = (
        f"import {{ coveredYears }} from {json.dumps(EVIDENCE.as_uri())};"
        f"console.log(JSON.stringify(coveredYears({json.dumps(started)})));"
    )
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(NODE), "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("started", "first", "last"),
    [
        ("2026-11-09 15:23:13 CET", 2025, 2031),
        ("2027-01-01 00:30:00 CET", 2026, 2032),
    ],
)
def test_the_window_is_the_starts_year_one_before_and_five_after(
    started: str, first: int, last: int
) -> None:
    """The years follow the start's own year, not the clock of the moment the log is written."""
    done = covered_years(started)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == list(range(first, last + 1))


@pytest.mark.parametrize("started", [None, "?", ""])
def test_a_start_without_a_year_stops_the_capture(started: object) -> None:
    """No window from a missing or malformed start: rule 4 would silently cover no year."""
    done = covered_years(started)
    assert done.returncode != 0
    assert "holds no year" in done.stderr
