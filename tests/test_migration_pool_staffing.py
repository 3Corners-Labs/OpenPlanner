"""Migration 0111 (ADR 0122 step 2): pool staffing tables and the pool on a slot, expand only.

Upgrading adds a nullable time zone to pools, two staffing tables and two nullable columns on
slots without touching a row; the legacy unique index keeps its meaning for the previous image
(which writes no pool) while a pool slot lives beside a legacy slot; downgrading restores the old
index and refuses while a slot carries a pool (ADR 0116, the precedent is migration 0010).
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

START = "2026-01-05 08:00:00.000000"
END = "2026-01-05 08:30:00.000000"
UNTIL = "2026-01-05 08:35:00.000000"
SLOT = "INSERT INTO slots (group_id, start, end, capacity_snapshot, kind) VALUES (1, ?, ?, 1, ?)"


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0110 with a group, a legacy slot, a direct slot and a counted pool."""
    command.upgrade(alembic_config, "0110")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        database.execute(
            "INSERT INTO organisations (id, name, slug, idp_key) VALUES (1, 'Org', 'org', 'org')"
        )
        database.execute(
            "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
            " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, idp_key)"
            " VALUES (1, 1, 'Balie', 'balie', 'Europe/Amsterdam', 30, 60, 60, 'optional',"
            " 'contact_center', 'balie')"
        )
        database.execute(
            "INSERT INTO capacity_pools (id, organisation_id, name, mode, code)"
            " VALUES (1, 1, 'Loket', 'counted', 'loket')"
        )
        database.execute(SLOT, (START, END, "scheduled"))
        database.execute(SLOT, (START, END, "direct"))
        database.commit()
    return path


def columns(path: Path, table: str) -> list[str]:
    """Return the column names of ``table``."""
    with closing(sqlite3.connect(path)) as database:
        return [row[1] for row in database.execute(f"PRAGMA table_info({table})")]


def index_sql(path: Path, name: str) -> str | None:
    """Return the SQL of index ``name``, ``None`` when it does not exist."""
    with closing(sqlite3.connect(path)) as database:
        row = database.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
    return None if row is None else str(row[0])


def test_upgrade_adds_columns_tables_and_indexes_and_leaves_every_row_legacy(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")

    assert "timezone" in columns(old_database, "capacity_pools")
    assert {"capacity_pool_id", "occupied_until"} <= set(columns(old_database, "slots"))
    assert columns(old_database, "pool_staffing_rules") == [
        "id",
        "pool_id",
        "weekday",
        "opens",
        "closes",
        "units",
        "label",
        "offer",
        "valid_from",
        "valid_to",
    ]
    assert columns(old_database, "pool_staffing_overrides") == [
        "id",
        "pool_id",
        "day",
        "opens",
        "closes",
        "units",
        "label",
        "offer",
    ]
    with closing(sqlite3.connect(old_database)) as database:
        slots = database.execute("SELECT capacity_pool_id, occupied_until FROM slots").fetchall()
        zones = database.execute("SELECT timezone FROM capacity_pools").fetchall()
    assert slots == [(None, None), (None, None)]
    assert zones == [(None,)]
    legacy = index_sql(old_database, "uq_slots_group_start_scheduled")
    assert legacy is not None
    assert "capacity_pool_id IS NULL" in legacy
    assert index_sql(old_database, "uq_slots_pool") is not None
    assert index_sql(old_database, "ix_slots_pool_start") is not None


def test_the_previous_image_still_writes_and_is_held_by_the_same_rules(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(SLOT, ("2026-01-05 09:00:00.000000", END, "scheduled"))  # only old columns
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(SLOT, (START, END, "scheduled"))  # one legacy slot per start
        database.execute(SLOT, (START, END, "direct"))  # direct calls each have their own slot


def test_a_pool_slot_lives_beside_a_legacy_slot_with_a_key_of_its_own(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    pooled = (
        "INSERT INTO slots (group_id, start, end, capacity_snapshot, kind, capacity_pool_id,"
        " occupied_until) VALUES (1, ?, ?, 2, 'scheduled', 1, ?)"
    )
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(pooled, (START, END, UNTIL))
        database.execute(pooled, (START, END, "2026-01-05 08:40:00.000000"))  # another length
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(pooled, (START, END, UNTIL))  # same pool, service, start, length


def test_staffing_goes_with_its_pool_and_keeps_the_checks_of_a_service(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    rule = (
        "INSERT INTO pool_staffing_rules (pool_id, weekday, opens, closes, units)"
        " VALUES (1, ?, ?, ?, ?)"
    )
    with closing(sqlite3.connect(old_database)) as database:
        database.execute("PRAGMA foreign_keys = ON")
        database.execute(rule, (0, "09:00:00.000000", "12:00:00.000000", 2))
        for bad in [
            (7, "09:00:00.000000", "12:00:00.000000", 2),  # weekday
            (0, "12:00:00.000000", "09:00:00.000000", 2),  # opens before closes
            (0, "09:00:00.000000", "12:00:00.000000", -1),  # units
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(rule, bad)
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO pool_staffing_overrides (pool_id, day, opens, closes, units)"
                " VALUES (1, '2026-03-02', '12:00:00.000000', '09:00:00.000000', 1)"
            )
        for partial in (
            "INSERT INTO pool_staffing_overrides (pool_id, day, opens)"
            " VALUES (1, '2026-03-02', '09:00')",
            "INSERT INTO pool_staffing_overrides (pool_id, day, units) VALUES (1, '2026-03-02', 1)",
        ):
            with pytest.raises(sqlite3.IntegrityError):  # a window is all or none of its columns
                database.execute(partial)
        database.execute(
            "INSERT INTO pool_staffing_overrides (pool_id, day) VALUES (1, '2026-03-02')"
        )
        database.execute("DELETE FROM capacity_pools WHERE id = 1")
        assert database.execute("SELECT COUNT(*) FROM pool_staffing_rules").fetchone() == (0,)
        assert database.execute("SELECT COUNT(*) FROM pool_staffing_overrides").fetchone() == (0,)


def test_downgrade_restores_the_old_schema_and_upgrading_again_works(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0110")

    assert "timezone" not in columns(old_database, "capacity_pools")
    assert not {"capacity_pool_id", "occupied_until"} & set(columns(old_database, "slots"))
    assert columns(old_database, "pool_staffing_rules") == []
    assert columns(old_database, "pool_staffing_overrides") == []
    restored = index_sql(old_database, "uq_slots_group_start_scheduled")
    assert restored is not None
    assert "capacity_pool_id" not in restored
    assert index_sql(old_database, "uq_slots_pool") is None
    assert index_sql(old_database, "ix_slots_pool_start") is None
    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute("SELECT COUNT(*) FROM slots").fetchone() == (2,)

    command.upgrade(alembic_config, "head")

    assert "capacity_pool_id" in columns(old_database, "slots")


def test_downgrade_refuses_while_a_slot_carries_a_pool(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute(
            "INSERT INTO slots (group_id, start, end, capacity_snapshot, capacity_pool_id,"
            " occupied_until) VALUES (1, ?, ?, 2, 1, ?)",
            (START, END, UNTIL),
        )
        database.commit()

    with pytest.raises(RuntimeError, match="pool slots exist"):
        command.downgrade(alembic_config, "0110")

    assert "capacity_pool_id" in columns(old_database, "slots")
