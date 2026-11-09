"""The years a role-manuals capture covers in its screenshots, and the capture that needs them.

Rule 4 of ``redact.js`` covers the year of a capture, the one before and the five after it
(``coveredYears`` in ``evidence.mjs``). The window comes from the capture's recorded start, so
the screenshots and a log rewritten later name the same years. ``capture.mjs`` checks that start
once: a start without a year stops a run before it needs a stack, and a log rewrite before it
writes the log. Runs the scripts in node, as the capture does, on a workspace outside the
checkout.
"""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.support.skill_scripts import SKILLS, manuals_workspace

NODE = shutil.which("node")
SCRIPTS = SKILLS / "role-manuals" / "scripts"
EVIDENCE = SCRIPTS / "evidence.mjs"
CAPTURE = SCRIPTS / "capture.mjs"
STACK = {
    "id": "20261109-150059",
    "instance": "c2061c63-7d63-4e9f-8647-8142e89723e0",
    "web": "http://127.0.0.1:8022",
    "keycloak": "http://127.0.0.1:8102",
    "mail": "http://127.0.0.1:8047",
    "build": {"commit": "0" * 40, "image": "openplanner:verify", "localChanges": "no"},
}
"""The run a recorded capture names; no stack runs, the log only quotes it."""

pytestmark = pytest.mark.skipif(NODE is None, reason="the capture scripts run in node")


def node(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run node with ``args`` and return the finished process."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(NODE), *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def covered_years(argument: str) -> subprocess.CompletedProcess[str]:
    """Run ``coveredYears(<argument>)`` in node, the argument as JS source; JSON on stdout."""
    script = (
        f"import {{ coveredYears }} from {json.dumps(EVIDENCE.as_uri())};"
        f"console.log(JSON.stringify(coveredYears({argument})));"
    )
    return node("--input-type=module", "-e", script)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace outside the checkout, with no variables and no capture yet."""
    return manuals_workspace(tmp_path / "manuals", {})


def capture(workspace: Path, started: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``capture.mjs`` on ``workspace`` after a recorded capture that started at ``started``."""
    state: dict[str, object] = {"stack": STACK, "installation": STACK["instance"], "records": {}}
    if started is not None:
        state["started"] = started
    (workspace / "evidence" / "state.json").write_text(json.dumps(state))
    return node(str(CAPTURE), str(workspace), *args, timeout=60)


def stopped_on_the_start(done: subprocess.CompletedProcess[str], workspace: Path) -> bool:
    """Whether the script stopped as a precondition (exit 2, one line) and wrote no log."""
    message = done.stderr.strip()
    return (
        done.returncode == 2
        and message.endswith("holds no year")
        and "\n" not in message
        and not (workspace / "evidence" / "log.md").exists()
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
    done = covered_years(json.dumps(started))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == list(range(first, last + 1))


@pytest.mark.parametrize("argument", ["", "undefined", "null", '"?"', '""', '"09-11-2026 10:00"'])
def test_a_start_without_a_year_has_no_window(argument: str) -> None:
    """No window from a missing or malformed start, and no fallback to today's clock."""
    done = covered_years(argument)
    assert done.returncode != 0
    assert "holds no year" in done.stderr


@pytest.mark.parametrize("started", [None, "?", "09-11-2026 10:00"])
def test_a_log_rewrite_stops_on_a_start_without_a_year(
    workspace: Path, started: str | None
) -> None:
    """``--log-only`` writes no log that would name years the capture never covered."""
    done = capture(workspace, started, "--log-only")
    assert stopped_on_the_start(done, workspace), done.stderr


@pytest.mark.parametrize("started", ["?", "09-11-2026 10:00"])
def test_a_run_stops_on_a_recorded_start_without_a_year_before_it_needs_a_stack(
    workspace: Path, started: str
) -> None:
    """A run on such a state stops at the start, not later at the stack or in the story."""
    done = capture(workspace, started)
    assert stopped_on_the_start(done, workspace), done.stderr


def test_a_log_rewrite_names_the_start_and_its_years(workspace: Path) -> None:
    """A valid start gives the log its moment and the window of that start, never today's.

    The start lies ten years after today, so its window never overlaps the clock's.
    """
    year = date.today().year + 10
    started = f"{year}-03-04 10:00:00 CET"
    done = capture(workspace, started, "--log-only")
    assert done.returncode == 0, done.stderr
    log = (workspace / "evidence" / "log.md").read_text()
    assert f"Opname gestart {started}" in log
    assert f"het jaartal ({year - 1} tot en met {year + 5})" in log
