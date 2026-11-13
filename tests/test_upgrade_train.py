"""Upgrade and rollback proof of the services-and-flows train (ADR 0121 step 8, ADR 0116).

The train's migrations (0109 to 0115) run on a database that a pre-train image holds: the
upgrade fixture of ADR 0116 point 7 (groups, closures, bookings in every status with one purged,
pending jobs, a value of every class C and D setting, an identity provider, an API client, audit
events), taken back to 0108, the revision before the train. The proof, per the compatibility
matrix in ``docs/service-flows-compatibility.md``:

* upgrading changes no value of any row a pre-train table held, adds only nullable or defaulted
  columns to two pre-train tables (``bookings`` and ``slots``), and keeps the invariants of the
  upgrade matrix (personal data on live bookings only, every pending job claimable, every stored
  setting usable);
* downgrading back to 0108 gives the pre-train database back value for value, and upgrading
  again gives the same backfill;
* a downgrade is one transaction: a refusal on a record that only the gated paths write leaves
  the database at head with everything in it, never half way;
* the previous image (N-1) still reads and writes: every pre-train column keeps its type and
  never becomes stricter, and a row written with the pre-train columns only is stored, read and
  claimed by the current code.
"""

import asyncio
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.db.engine import create_engine
from app.adapters.db.upgrade_fixture import (
    FixtureState,
    compare_states,
    inspect_fixture,
    seed_upgrade_fixture,
)
from tests.support.domain import NOW

PRE_TRAIN = "0108"
"""The revision before ADR 0121's first migration (0109): the schema of a pre-train image."""
TRAIN_TOUCHED = {"bookings", "slots"}
"""The only pre-train tables the train adds columns to; every other one is left as it was."""
LATER = NOW + timedelta(days=3650)
"""A moment after every pending job's ``run_at``, as the upgrade check claims."""
Rows = dict[str, list[tuple[object, ...]]]


def _columns(database: sqlite3.Connection, table: str) -> list[tuple[str, str, int, object]]:
    """Return ``(name, type, notnull, default)`` of every column of ``table``."""
    return [
        (row[1], row[2], row[3], row[4])
        for row in database.execute(f'PRAGMA table_info("{table}")')
    ]


def _tables(database: sqlite3.Connection) -> list[str]:
    """Return the data tables, without Alembic's and SQLite's own."""
    return [
        name
        for (name,) in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name"
        )
    ]


def schema(path: Path) -> dict[str, list[tuple[str, str, int, object]]]:
    """Return the columns of every table of the database at ``path``."""
    with closing(sqlite3.connect(path)) as database:
        return {table: _columns(database, table) for table in _tables(database)}


def snapshot(path: Path, shape: dict[str, list[tuple[str, str, int, object]]]) -> Rows:
    """Return every row of the tables of ``shape``, with the columns of ``shape`` only.

    Rows are sorted on their text form, so a table that a migration copied (SQLite's way of
    changing a table) compares equal whatever order the copy stored them in.
    """
    with closing(sqlite3.connect(path)) as database:
        found = {}
        for table, columns in shape.items():
            names = ", ".join(f'"{name}"' for name, *_ in columns)
            rows = database.execute(f'SELECT {names} FROM "{table}"').fetchall()  # noqa: S608
            found[table] = sorted(rows, key=repr)
    return found


def revision(path: Path) -> str:
    """Return the Alembic revision of the database at ``path``."""
    with closing(sqlite3.connect(path)) as database:
        return str(database.execute("SELECT version_num FROM alembic_version").fetchone()[0])


async def _seed(url: str) -> None:
    """Write the upgrade fixture with the current code, as a release does."""
    engine = create_engine(url)
    try:
        await seed_upgrade_fixture(engine, NOW, b"k" * 32, version="0.5.1", commit="pretrain")
    finally:
        await engine.dispose()


@pytest.fixture
def database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return the path of the database ``alembic_config`` migrates."""
    return tmp_path / "migrate.db"


@pytest.fixture
def pre_train(alembic_config: Config, database: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a database at 0108 holding the upgrade fixture: what a pre-train image holds.

    The fixture is written at head (the seed speaks the current schema) and taken back to 0108;
    the downgrade removes what the train added, so what is left is a pre-train database.
    """
    monkeypatch.setenv("ENVIRONMENT", "dev")
    command.upgrade(alembic_config, "head")
    asyncio.run(_seed(f"sqlite+aiosqlite:///{database}"))
    command.downgrade(alembic_config, PRE_TRAIN)
    assert revision(database) == PRE_TRAIN
    return database


@pytest.fixture
def pre_train_shape(pre_train: Path) -> dict[str, list[tuple[str, str, int, object]]]:
    """Return the schema of the pre-train database."""
    return schema(pre_train)


def _state(path: Path) -> FixtureState:
    """Return the upgrade matrix's reading of the database at ``path``."""
    return asyncio.run(inspect_fixture(f"sqlite+aiosqlite:///{path}", LATER))


def test_the_pre_train_fixture_holds_every_kind_of_row(pre_train: Path) -> None:
    rows = snapshot(pre_train, schema(pre_train))

    for table in ("organisations", "groups", "schedule_rules", "slots", "bookings", "jobs"):
        assert rows[table], table
    for table in ("organisation_settings", "platform_settings", "closures", "audit_events"):
        assert rows[table], table
    assert "service_flows" not in rows  # the train's tables are gone at 0108


def test_upgrading_keeps_every_pre_train_value_and_the_matrix_invariants(
    alembic_config: Config,
    pre_train: Path,
    pre_train_shape: dict[str, list[tuple[str, str, int, object]]],
) -> None:
    before_rows = snapshot(pre_train, pre_train_shape)
    before = _state(pre_train)

    command.upgrade(alembic_config, "head")

    assert snapshot(pre_train, pre_train_shape) == before_rows
    after = _state(pre_train)
    assert compare_states(before, after) == []
    assert after.pending_jobs == before.pending_jobs > 0
    assert after.settings_unusable == []


def test_every_pre_train_booking_and_slot_reads_as_legacy_after_the_upgrade(
    alembic_config: Config, pre_train: Path
) -> None:
    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(pre_train)) as database:
        flows = database.execute(
            "SELECT COUNT(*) FROM bookings WHERE flow_id IS NOT NULL OR flow_revision IS NOT NULL"
        ).fetchone()
        pooled = database.execute(
            "SELECT COUNT(*) FROM slots"
            " WHERE capacity_pool_id IS NOT NULL OR occupied_until IS NOT NULL"
        ).fetchone()
        groups = database.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        legacy_pools = database.execute(
            "SELECT COUNT(DISTINCT group_id) FROM capacity_pools WHERE mode = 'legacy_group'"
        ).fetchone()[0]
        requests = database.execute("SELECT COUNT(*) FROM service_requests").fetchone()
    assert flows == (0,)  # NULL: the service's default flow for the booking's path
    assert pooled == (0,)  # every slot is a legacy slot
    assert legacy_pools == groups  # one legacy pool per service, nothing shared
    assert requests == (0,)  # no request is invented for an old booking


def test_downgrading_gives_the_pre_train_database_back_and_upgrading_again_the_same_backfill(
    alembic_config: Config,
    pre_train: Path,
    pre_train_shape: dict[str, list[tuple[str, str, int, object]]],
) -> None:
    before_rows = snapshot(pre_train, pre_train_shape)
    command.upgrade(alembic_config, "head")
    # The backfill stamps a revision with the moment of the migration; everything else repeats.
    head_shape = {
        table: [column for column in columns if column[0] != "published_at"]
        for table, columns in schema(pre_train).items()
    }
    first = snapshot(pre_train, head_shape)

    command.downgrade(alembic_config, PRE_TRAIN)

    assert schema(pre_train) == pre_train_shape
    assert snapshot(pre_train, pre_train_shape) == before_rows
    command.upgrade(alembic_config, "head")
    assert snapshot(pre_train, head_shape) == first


def test_the_train_changes_only_bookings_and_slots_of_the_pre_train_tables_and_only_additively(
    alembic_config: Config,
    pre_train: Path,
    pre_train_shape: dict[str, list[tuple[str, str, int, object]]],
) -> None:
    command.upgrade(alembic_config, "head")
    head = schema(pre_train)

    changed = {table for table, columns in pre_train_shape.items() if head[table] != columns}
    assert changed == TRAIN_TOUCHED
    for table, columns in pre_train_shape.items():
        old = {name: (kind, notnull) for name, kind, notnull, _ in columns}
        now = {name: (kind, notnull, default) for name, kind, notnull, default in head[table]}
        for name, (kind, notnull) in old.items():
            assert now[name][:2] == (kind, notnull), f"{table}.{name} changed"
        for name in now.keys() - old.keys():
            _, notnull, default = now[name]
            assert not notnull or default is not None, f"{table}.{name} needs a value"


def test_the_previous_image_writes_and_the_current_code_reads_and_claims_it(
    alembic_config: Config, pre_train: Path
) -> None:
    command.upgrade(alembic_config, "head")
    before = _state(pre_train)
    with closing(sqlite3.connect(pre_train)) as database:
        group = database.execute("SELECT id FROM groups ORDER BY id LIMIT 1").fetchone()[0]
        # Exactly the columns the pre-train schema has: what the previous image inserts.
        slot = database.execute(
            'INSERT INTO slots (group_id, start, "end", capacity_snapshot, kind)'
            " VALUES (?, '2030-01-07 08:07:00.000000', '2030-01-07 08:37:00.000000', 1,"
            " 'scheduled')",
            (group,),
        ).lastrowid
        booking = database.execute(
            "INSERT INTO bookings (slot_id, group_id, status, locale, management_token_hash,"
            " management_token_salt, public_ref, created_at, consents, first_name, last_name,"
            " email) VALUES (?, ?, 'confirmed', 'nl', 'h-n1', 's-n1', 'n-1-ref',"
            " '2030-01-01 08:00:00.000000', '', 'Voorbeeld', 'Deelnemer', 'n1@example.org')",
            (slot, group),
        ).lastrowid
        database.execute(
            "INSERT INTO jobs (type, booking_id, run_at, status, attempts, pinned_start)"
            " VALUES ('purge_pii', ?, '2030-01-08 08:37:00.000000', 'pending', 0, NULL)",
            (booking,),
        )
        database.commit()

    after = _state(pre_train)

    assert after.live_with_data == before.live_with_data + 1
    assert after.pending_jobs == after.claimable_jobs == before.pending_jobs + 1
    assert after.live_without_data == after.purged_with_data == 0


@pytest.fixture
def at_head_with_gated_records(alembic_config: Config, pre_train: Path) -> Path:
    """Return the database at head holding a pool slot, which only the gated pool path writes."""
    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(pre_train)) as database:
        group, organisation = database.execute(
            "SELECT id, organisation_id FROM groups ORDER BY id LIMIT 1"
        ).fetchone()
        pool = database.execute(
            "INSERT INTO capacity_pools (organisation_id, name, mode, code, timezone)"
            " VALUES (?, 'Matrix', 'counted', 'matrix', 'Europe/Amsterdam')",
            (organisation,),
        ).lastrowid
        database.execute(
            'INSERT INTO slots (group_id, start, "end", capacity_snapshot, kind,'
            " capacity_pool_id, occupied_until) VALUES (?, '2030-02-02 08:00:00.000000',"
            " '2030-02-02 08:30:00.000000', 1, 'scheduled', ?, '2030-02-02 08:35:00.000000')",
            (group, pool),
        )
        database.commit()
    return pre_train


def test_a_refused_downgrade_stops_at_the_refusing_step_keeps_what_it_guards_and_goes_up_again(
    alembic_config: Config,
    at_head_with_gated_records: Path,
    pre_train_shape: dict[str, list[tuple[str, str, int, object]]],
) -> None:
    path = at_head_with_gated_records
    pre_train_rows = snapshot(path, pre_train_shape)
    pool_slots = "SELECT COUNT(*) FROM slots WHERE capacity_pool_id IS NOT NULL"

    # 0115 to 0112 go down first. SQLite runs their schema changes outside the migration's
    # transaction, so they stay done when 0111 refuses: the database stops at 0111, not at head
    # (the runbook's rollback section; the image swap without a downgrade is the normal path).
    with pytest.raises(RuntimeError, match="pool slots exist"):
        command.downgrade(alembic_config, PRE_TRAIN)

    assert revision(path) == "0111"
    with closing(sqlite3.connect(path)) as database:
        assert database.execute(pool_slots).fetchone() == (1,)
    command.upgrade(alembic_config, "head")
    assert snapshot(path, pre_train_shape) == pre_train_rows
    with closing(sqlite3.connect(path)) as database:
        assert database.execute(pool_slots).fetchone() == (1,)
