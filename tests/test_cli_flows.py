"""The ``ensure-flows`` command (ADR 0121 step 2e): the repair for a group an older image made."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.cli import main
from tests.support.database import migrated_database


def _group_without_rows(url: str) -> Path:
    """Add an organisation and a group the way an image without flows writes them."""
    path = Path(url.removeprefix("sqlite+aiosqlite:///"))
    with closing(sqlite3.connect(path)) as database:
        database.execute("INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme')")
        database.execute(
            "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
            " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, idp_key)"
            " VALUES (1, 1, 'Geheime naam', 'balie', 'Europe/Amsterdam', 30, 60, 60, 'optional',"
            " 'contact_center', 'balie')"
        )
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity, offer)"
            " VALUES (1, 0, '09:00:00', '10:00:00', 1, 'both')"
        )
        database.commit()
    return path


def _counts(path: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(path)) as database:
        pools = database.execute("SELECT count(*) FROM capacity_pools").fetchone()[0]
        flows = database.execute("SELECT count(*) FROM service_flows").fetchone()[0]
    return pools, flows


def test_the_command_gives_a_group_its_rows_and_says_only_how_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _group_without_rows(migrated_database(tmp_path, monkeypatch, "cli-flows"))
    assert _counts(path) == (0, 0)

    assert main(["ensure-flows"]) == 0

    out = capsys.readouterr().out
    assert out == "ensure-flows: 1 groups received pool or flow rows\n"
    assert "Geheime" not in out
    assert _counts(path) == (1, 2)  # one pool, the appointment and the direct flow


def test_running_it_again_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _group_without_rows(migrated_database(tmp_path, monkeypatch, "cli-flows-twice"))
    assert main(["ensure-flows"]) == 0
    capsys.readouterr()

    assert main(["ensure-flows"]) == 0

    assert capsys.readouterr().out == "ensure-flows: 0 groups received pool or flow rows\n"
    assert _counts(path) == (1, 2)
