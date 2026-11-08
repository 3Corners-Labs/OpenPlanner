"""The ``usage-backfill`` command (ADR 0117 point 5): what a host runs to see it done."""

import pytest

from app.cli import main
from app.config import Settings


def test_the_command_reports_what_it_did(
    settings: Settings,
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pathlib import Path

    from tests.support.database import migrated_database

    migrated_database(Path(str(tmp_path)), monkeypatch, "cli-usage")

    assert main(["usage-backfill"]) == 0

    assert capsys.readouterr().out == "usage-backfill: 0 bookings looked at, 0 facts written\n"


def test_the_command_says_so_when_reporting_is_off(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    from tests.support.database import migrated_database

    migrated_database(Path(str(tmp_path)), monkeypatch, "cli-usage-off")
    monkeypatch.setenv("REPORTING_ENABLED", "false")

    assert main(["usage-backfill"]) == 0

    assert "switched off" in capsys.readouterr().out
