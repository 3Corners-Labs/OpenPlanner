from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    monkeypatch.setenv("ENVIRONMENT", "test")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_upgrade_and_downgrade_on_fresh_database(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_single_head(alembic_config: Config) -> None:
    assert len(ScriptDirectory.from_config(alembic_config).get_heads()) == 1


def test_models_match_migrations(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_upgrade_and_downgrade_with_data(alembic_config: Config, tmp_path: Path) -> None:
    # SQLite rebuilds tables for some ALTERs; with foreign keys on, that must still work
    # when rows reference the rebuilt table (an empty database hides the problem).
    import sqlite3

    command.upgrade(alembic_config, "0006")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode, wxcc_queue_id)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional', 'q-1');
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)
            VALUES (1, 0, '09:00:00', '10:00:00', 1);
        INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
            VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 1);
        INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
            management_token_salt, created_at, consents)
            VALUES (1, 1, 1, 'confirmed', 'nl', 'h', 's', '2026-01-01 08:00:00', '');
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0006")
    command.upgrade(alembic_config, "head")

    database = sqlite3.connect(tmp_path / "migrate.db")
    routing = database.execute("SELECT wxcc_routing_value FROM groups").fetchone()
    booking = database.execute("SELECT slot_id FROM bookings").fetchone()
    database.close()
    assert routing == ("q-1",)  # the old queue id became the routing value
    assert booking == (1,)


def test_blocks_without_agents_survive_upgrade_and_are_removed_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    import sqlite3

    command.upgrade(alembic_config, "0017")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)
            VALUES (1, 0, '09:00:00', '12:00:00', 3);
        INSERT INTO schedule_overrides (group_id, day, opens, closes, capacity)
            VALUES (1, '2026-01-07', '09:00:00', '10:00:00', 2);
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 0, '12:00:00', '12:30:00', 0)"
        )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "0018")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity, label)
            VALUES (1, 0, '12:00:00', '12:30:00', 0, 'Lunch');
        INSERT INTO schedule_overrides (group_id, day, opens, closes, capacity, label)
            VALUES (1, '2026-01-08', '09:00:00', '10:00:00', 0, 'Heidag');
        INSERT INTO schedule_overrides (group_id, day) VALUES (1, '2026-01-09');
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 1, '09:00:00', '10:00:00', -1)"
        )
    database.commit()
    database.close()

    command.downgrade(alembic_config, "0017")
    database = sqlite3.connect(tmp_path / "migrate.db")
    rules = database.execute("SELECT capacity FROM schedule_rules").fetchall()
    overrides = database.execute("SELECT day, capacity FROM schedule_overrides").fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 2, '09:00:00', '10:00:00', 0)"
        )
    database.close()
    assert rules == [(3,)]
    assert sorted(overrides) == [("2026-01-07", 2), ("2026-01-09", None)]
