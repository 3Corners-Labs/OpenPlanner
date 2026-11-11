"""Migration 0109 (ADR 0121 step 2b): the tables, the booking columns and the backfill.

A database at 0108 with groups of every kind gets one legacy pool per group and the default
flows of what the group offers today; downgrading removes them again and upgrading again gives
the same result. The backfilled revision is compared with ``legacy_revision`` of the domain, so
the literal values of the migration and the rule of the domain cannot drift apart.
"""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.default_flows import bind_pool, default_flow_set
from app.domain.scheduling import DispatchKind as GroupDispatchKind
from app.domain.service_flows import DispatchKind, IntakeMode, legacy_revision

EVERYTHING = Scope(everything=True)
LONG_NAME = "Een heel lange naam van een groep " * 3

GROUPS = [
    # id, name, slot minutes, dispatch kind, archived
    (1, "Balie", 30, "contact_center", None),
    (2, "Loket", 20, "openplanner_queue", None),
    (3, "Oud", 15, "contact_center", "2026-01-01 00:00:00"),
    (4, "Lunch", 30, "contact_center", None),
    (5, "Verlopen", 30, "contact_center", None),
    (6, LONG_NAME, 45, "openplanner_queue", None),
    (7, "Alleen direct", 30, "contact_center", None),
]
RULES = [
    # group, offer, capacity, valid_to
    (1, "scheduled", 2, None),
    (2, "both", 2, None),
    (3, "direct", 1, None),
    (4, "direct", 0, None),  # a block without agents offers nothing
    (5, "direct", 1, "2020-01-01"),  # ended long ago: still configuration
    (6, "both", 1, "2999-01-01"),
    (7, "direct", 3, None),
]
NEW_TABLES = {
    "skills",
    "staff_skills",
    "capacity_pools",
    "capacity_pool_skills",
    "service_flows",
    "flow_revisions",
    "flow_revision_skills",
    "subject_flows",
    "subject_flow_skills",
}


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0108 holding the groups above and one booking."""
    command.upgrade(alembic_config, "0108")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        database.execute("INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme')")
        for group_id, name, minutes, dispatch, archived in GROUPS:
            database.execute(
                "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
                " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, archived_at,"
                " idp_key) VALUES (?, 1, ?, ?, 'Europe/Amsterdam', ?, 60, 60, 'optional', ?, ?, ?)",
                (group_id, name, f"g{group_id}", minutes, dispatch, archived, f"g{group_id}"),
            )
        for group_id, offer, capacity, valid_to in RULES:
            database.execute(
                "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity, offer,"
                " valid_to) VALUES (?, 0, '09:00:00', '10:00:00', ?, ?, ?)",
                (group_id, capacity, offer, valid_to),
            )
        database.execute(
            'INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)'
            " VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 1)"
        )
        database.execute(
            "INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,"
            " management_token_salt, public_ref, created_at, consents)"
            " VALUES (1, 1, 1, 'confirmed', 'nl', 'h', 's', 'ref-1', '2026-01-01 08:00:00', '')"
        )
        database.commit()
    return path


@pytest.fixture
def upgraded(alembic_config: Config, old_database: Path) -> Path:
    """Return the database of ``old_database`` upgraded to head (outside any event loop)."""
    command.upgrade(alembic_config, "head")
    return old_database


def rows(path: Path, query: str) -> list[tuple[object, ...]]:
    """Return the rows of ``query`` on the database at ``path``."""
    with closing(sqlite3.connect(path)) as database:
        return database.execute(query).fetchall()


def flows(path: Path) -> dict[object, list[tuple[object, ...]]]:
    """Return each group's flows as ``(code, state, default appointment, default on demand)``."""
    found: dict[object, list[tuple[object, ...]]] = {}
    for group_id, *flow in rows(
        path,
        "SELECT group_id, code, state, default_appointment, default_on_demand"
        " FROM service_flows ORDER BY group_id, id",
    ):
        found.setdefault(group_id, []).append(tuple(flow))
    return found


def test_every_group_gets_a_legacy_pool_and_its_default_flows(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")

    appointment = ("appointment", "active", 1, 0)
    direct = ("direct", "active", 0, 1)
    archived = [("appointment", "archived", 1, 0), ("direct", "archived", 0, 1)]
    assert flows(old_database) == {
        1: [appointment],
        2: [appointment, direct],
        3: archived,
        4: [appointment],  # a block without agents offers no direct call
        5: [appointment, direct],  # the flow is configuration: validity dates play no part
        6: [appointment, direct],
        7: [appointment, direct],  # a direct-only block still gets the appointment flow
    }
    pools = rows(
        old_database, "SELECT group_id, organisation_id, mode, archived_at FROM capacity_pools"
    )
    assert sorted(pools) == [(g, 1, "legacy_group", None) for g in range(1, 8)]
    assert rows(old_database, "SELECT count(*) FROM staff_skills") == [(0,)]


def test_the_revision_is_built_from_the_groups_present_settings(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")

    found = rows(
        old_database,
        "SELECT g.id, f.code, r.intake_modes, r.duration_minutes, r.dispatch, r.legacy,"
        " r.published_at IS NOT NULL, r.revision, f.current_revision, p.group_id"
        " FROM flow_revisions r JOIN service_flows f ON f.id = r.flow_id"
        " JOIN groups g ON g.id = f.group_id JOIN capacity_pools p ON p.id = r.capacity_pool_id"
        " ORDER BY g.id, f.id",
    )
    assert found[0] == (1, "appointment", "appointment", 30, "contact_center", 1, 1, 1, 1, 1)
    assert found[1][:6] == (2, "appointment", "appointment", 20, "own_queue", 1)
    assert found[2][:6] == (2, "direct", "on_demand", 20, "own_queue", 1)
    assert all(row[9] == row[0] for row in found)  # each flow draws on its own group's pool
    assert rows(
        old_database, "SELECT DISTINCT current_revision, draft_revision FROM service_flows"
    ) == [(1, None)]  # every backfilled revision is the published one; nobody is editing


def test_a_long_group_name_is_cut_to_what_a_label_may_hold(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")

    (name,) = rows(old_database, "SELECT name FROM capacity_pools WHERE group_id = 6")[0]
    (labels,) = rows(
        old_database,
        "SELECT r.labels FROM flow_revisions r JOIN service_flows f ON f.id = r.flow_id"
        " WHERE f.group_id = 6 AND f.code = 'appointment'",
    )[0]
    assert isinstance(name, str)
    assert 0 < len(name) <= 80
    assert LONG_NAME.startswith(name)
    assert isinstance(labels, str)
    assert name in labels


def test_existing_bookings_keep_no_flow(alembic_config: Config, old_database: Path) -> None:
    command.upgrade(alembic_config, "head")

    assert rows(old_database, "SELECT id, flow_id, flow_revision FROM bookings") == [
        (1, None, None)
    ]


def test_downgrade_removes_everything_and_upgrading_again_gives_the_same(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "head")
    first = flows(old_database)

    command.downgrade(alembic_config, "0108")
    tables = {name for (name,) in rows(old_database, "SELECT name FROM sqlite_master")}
    columns = {row[1] for row in rows(old_database, "PRAGMA table_info(bookings)")}
    assert tables.isdisjoint(NEW_TABLES)
    assert columns.isdisjoint({"flow_id", "flow_revision"})
    assert rows(old_database, "SELECT count(*) FROM bookings") == [(1,)]

    command.upgrade(alembic_config, "head")
    assert flows(old_database) == first


@pytest.mark.parametrize(
    ("group_id", "intake", "name", "minutes", "dispatch"),
    [
        (1, IntakeMode.APPOINTMENT, "Balie", 30, DispatchKind.CONTACT_CENTER),
        (2, IntakeMode.ON_DEMAND, "Loket", 20, DispatchKind.OWN_QUEUE),
    ],
)
async def test_the_backfilled_revision_is_what_the_domain_builds(
    upgraded: Path,
    group_id: int,
    intake: IntakeMode,
    name: str,
    minutes: int,
    dispatch: DispatchKind,
) -> None:
    """The literal values of the migration equal ``legacy_revision`` for the same group."""
    engine = create_engine(f"sqlite+aiosqlite:///{upgraded}", busy_timeout_ms=200)
    code = "appointment" if intake is IntakeMode.APPOINTMENT else "direct"
    async with SqlUnitOfWork(engine).read() as tx:
        (stored,) = [f for f in await tx.list_service_flows(EVERYTHING, group_id) if f.code == code]
        pools = {p.group_id: p for p in await tx.list_capacity_pools(EVERYTHING, 1)}
    await engine.dispose()

    pool = pools[group_id]
    assert pool.id is not None
    assert (pool.name, pool.mode.value) == (name, "legacy_group")
    expected = legacy_revision(
        labels={"nl": name, "en": name},
        intake_mode=intake,
        duration_minutes=minutes,
        capacity_pool_id=pool.id,
        dispatch=dispatch,
    )
    assert stored.id is not None
    assert stored.current.published_at is not None
    assert stored.current == replace(
        expected, flow_id=stored.id, published_at=stored.current.published_at
    )
    assert stored.default_for == {intake}


async def test_the_backfill_and_the_rule_for_new_groups_build_the_same(upgraded: Path) -> None:
    """For every fixture group ``default_flow_set`` gives the pool and revisions 0109 wrote.

    Migration 0109 and ``app.domain.default_flows`` are two statements of one rule (ADR 0121
    step 2e); this is the test that fails when one of them changes without the other.
    """
    direct = {g for g, offer, capacity, _ in RULES if offer in ("direct", "both") and capacity > 0}
    engine = create_engine(f"sqlite+aiosqlite:///{upgraded}", busy_timeout_ms=200)
    async with SqlUnitOfWork(engine).read() as tx:
        pools = {p.group_id: p for p in await tx.list_capacity_pools(EVERYTHING, 1)}
        stored = {g: await tx.list_service_flows(EVERYTHING, g) for g, *_ in GROUPS}
    await engine.dispose()

    for group_id, name, minutes, dispatch, archived in GROUPS:
        wanted = default_flow_set(
            group_id,
            1,
            name,
            minutes,
            GroupDispatchKind(dispatch),
            group_id in direct,
            archived is not None,
        )
        pool = pools[group_id]
        assert wanted.pool is not None
        assert pool.id is not None
        assert replace(wanted.pool, id=pool.id) == pool
        expected = bind_pool(wanted, pool.id)
        assert [f.code for f in stored[group_id]] == [f.code for f in expected]
        for found, rule in zip(stored[group_id], expected, strict=True):
            assert rule.current is not None
            assert found.current is not None
            assert (found.state, found.default_for) == (rule.state, rule.default_for)
            assert found.current == replace(
                rule.current, flow_id=found.id, published_at=found.current.published_at
            )
