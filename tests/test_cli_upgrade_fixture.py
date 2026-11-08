"""The upgrade fixture commands of ADR 0116 point 7: ``seed --upgrade-fixture``, the check."""

import json
from pathlib import Path

import pytest

from app.adapters.db.upgrade_fixture import FixtureState, inspect_fixture
from app.cli import UPGRADE_CHECK_AHEAD, _seed_upgrade_fixture, _upgrade_check, main
from app.config import Settings
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY", "k" * 40)
    url = migrated_database(tmp_path, monkeypatch, "fixture")
    return Settings(database_url=url, app_version="0.6.0", app_commit="abc1234")


def _file(settings: Settings) -> Path:
    return Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))


async def _state(settings: Settings) -> FixtureState:
    return await inspect_fixture(_file(settings), NOW + UPGRADE_CHECK_AHEAD)


async def test_the_seed_writes_the_fixture_once(migrated: Settings) -> None:
    report, ok = await _seed_upgrade_fixture(migrated, 0)
    again, refused = await _seed_upgrade_fixture(migrated, 0)

    assert ok
    assert report == "upgrade fixture written (0 extra bookings)\n"
    assert not refused
    assert again == "seed --upgrade-fixture needs an empty database\n"


async def test_the_seed_refuses_production(migrated: Settings) -> None:
    report, ok = await _seed_upgrade_fixture(migrated.model_copy(update={"environment": "prod"}), 0)

    assert not ok
    assert "only outside production" in report


async def test_the_check_saves_and_compares_a_state(migrated: Settings, tmp_path: Path) -> None:
    await _seed_upgrade_fixture(migrated, 0)
    state = tmp_path / "before.json"

    saved, ok = _upgrade_check(await _state(migrated), state, None)
    same, unchanged = _upgrade_check(await _state(migrated), None, state)

    assert ok
    assert unchanged
    assert saved.startswith("revision ")
    assert "FAIL" not in same
    assert json.loads(state.read_text())["purged"] == 1


async def test_the_check_fails_on_a_lost_row(migrated: Settings, tmp_path: Path) -> None:
    await _seed_upgrade_fixture(migrated, 0)
    state = tmp_path / "before.json"
    _upgrade_check(await _state(migrated), state, None)
    recorded = json.loads(state.read_text())
    recorded["rows"]["bookings"] += 1
    state.write_text(json.dumps(recorded))

    report, ok = _upgrade_check(await _state(migrated), None, state)

    assert not ok
    assert "FAIL table bookings" in report


def test_both_commands_run_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY", "k" * 40)
    url = migrated_database(tmp_path, monkeypatch, "command")
    monkeypatch.setenv("DATABASE_URL", url)

    assert main(["seed", "--upgrade-fixture", "--bookings", "4"]) == 0
    assert main(["upgrade-check", url.removeprefix("sqlite+aiosqlite:///")]) == 0

    out = capsys.readouterr().out
    assert "upgrade fixture written (4 extra bookings)" in out
    assert "bookings" in out.splitlines()[-1]
