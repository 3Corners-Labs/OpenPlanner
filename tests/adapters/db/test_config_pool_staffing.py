"""A counted pool's time zone and staffing in a configuration document (ADR 0122 step 3)."""

import json
from collections.abc import AsyncIterator
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    GroupRow,
    OrganisationRow,
    PoolStaffingOverrideRow,
    PoolStaffingRuleRow,
    SlotRow,
)
from tests.support.config_seed import give_default_flows, seed
from tests.support.config_service import HOST, NOW, export, service, transfer
from tests.support.database import migrated_database
from tests.support.flow_documents import encode


async def add_pool(session: AsyncSession, slug: str, code: str, **values: Any) -> CapacityPoolRow:
    """Add a counted pool to organisation ``slug`` and return its row."""
    organisation = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == slug))
    assert organisation is not None
    row = CapacityPoolRow(
        organisation_id=organisation.id, name=code.title(), mode="counted", code=code, **values
    )
    session.add(row)
    await session.flush()
    return row


async def seed_staffing(engine: AsyncEngine) -> None:
    """Two organisations that each have a counted pool ``loket``; acme's is staffed."""
    await seed(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add(OrganisationRow(name="Beta", slug="beta"))
        await session.flush()
        loket = await add_pool(session, "acme", "loket", timezone="Europe/Amsterdam")
        await add_pool(session, "acme", "bare")
        other = await add_pool(session, "beta", "loket", timezone="UTC")
        session.add_all(
            [
                PoolStaffingRuleRow(
                    pool_id=loket.id,
                    weekday=1,
                    opens=time(13),
                    closes=time(17),
                    units=2,
                    offer="both",
                ),
                PoolStaffingRuleRow(
                    pool_id=loket.id,
                    weekday=0,
                    opens=time(9),
                    closes=time(12),
                    units=3,
                    label="Ochtend",
                    valid_from=date(2026, 1, 1),
                ),
                PoolStaffingOverrideRow(pool_id=loket.id, day=date(2026, 12, 25)),
                PoolStaffingOverrideRow(
                    pool_id=loket.id,
                    day=date(2026, 12, 24),
                    opens=time(9),
                    closes=time(12),
                    units=1,
                    offer="direct",
                ),
                PoolStaffingRuleRow(
                    pool_id=other.id, weekday=2, opens=time(8), closes=time(9), units=9
                ),
            ]
        )
    await give_default_flows(engine, NOW)


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, busy_timeout_ms=200)
    await seed_staffing(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database(tmp_path, monkeypatch, "target"), busy_timeout_ms=200)
    yield engine
    await engine.dispose()


def pool_of(document: dict[str, Any], slug: str, code: str) -> dict[str, Any]:
    """Return the pool ``code`` of the organisation ``slug`` of a document."""
    organisation = next(o for o in document["organisations"] if o["slug"] == slug)
    found: dict[str, Any] = next(p for p in organisation["capacity_pools"] if p["code"] == code)
    return found


async def rows(engine: AsyncEngine, slug: str, code: str) -> tuple[list[Any], list[Any], Any]:
    """Return the stored blocks, exceptions and time zone of a pool, in the order of ``id``."""
    async with AsyncSession(engine) as session:
        pool = await session.scalar(
            select(CapacityPoolRow)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(OrganisationRow.slug == slug, CapacityPoolRow.code == code)
        )
        assert pool is not None
        rules = (
            await session.scalars(
                select(PoolStaffingRuleRow)
                .where(PoolStaffingRuleRow.pool_id == pool.id)
                .order_by(PoolStaffingRuleRow.id)
            )
        ).all()
        overrides = (
            await session.scalars(
                select(PoolStaffingOverrideRow)
                .where(PoolStaffingOverrideRow.pool_id == pool.id)
                .order_by(PoolStaffingOverrideRow.id)
            )
        ).all()
        return list(rules), list(overrides), pool.timezone


async def test_the_document_holds_the_staffing_in_one_order(source: AsyncEngine) -> None:
    exported = await export(source)

    loket = pool_of(exported, "acme", "loket")
    assert loket["timezone"] == "Europe/Amsterdam"
    assert [
        (r["weekday"], r["opens"], r["units"], r["offer"]) for r in loket["staffing_rules"]
    ] == [
        (0, "09:00", 3, "scheduled"),
        (1, "13:00", 2, "both"),
    ]
    assert loket["staffing_rules"][0]["label"] == "Ochtend"
    assert loket["staffing_rules"][0]["valid_from"] == "2026-01-01"
    assert [(o["day"], o["units"], o["offer"]) for o in loket["staffing_overrides"]] == [
        ("2026-12-24", 1, "direct"),
        ("2026-12-25", None, "scheduled"),
    ]
    bare = pool_of(exported, "acme", "bare")
    assert (bare["timezone"], bare["staffing_rules"], bare["staffing_overrides"]) == (None, [], [])


async def test_export_import_export_gives_the_same_document(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    exported = await export(source)

    applied = await transfer(source, target)

    assert applied.plan.refusals == (), applied.plan.refusals
    again = await export(target)
    assert again == exported
    rules, overrides, zone = await rows(target, "acme", "loket")
    assert (len(rules), len(overrides), zone) == (2, 2, "Europe/Amsterdam")


async def test_importing_into_the_same_installation_plans_nothing(source: AsyncEngine) -> None:
    plan = await service(source).plan(HOST, encode(await export(source)), None)

    assert plan.refusals == ()
    assert plan.changes == (), plan.lines()


async def test_a_document_of_before_the_staffing_leaves_it_as_it_is(source: AsyncEngine) -> None:
    document = await export(source)
    for organisation in document["organisations"]:
        for pool in organisation["capacity_pools"]:
            for name in ("timezone", "staffing_rules", "staffing_overrides"):
                pool.pop(name)
    document["mode"] = "replace"
    before = await rows(source, "acme", "loket")

    result = await service(source).apply(HOST, encode(document), None, None)

    assert result.plan.refusals == ()
    assert result.plan.changes == ()
    after = await rows(source, "acme", "loket")
    assert [r.id for r in after[0]] == [r.id for r in before[0]]
    assert after[2] == "Europe/Amsterdam"


async def test_a_list_the_document_names_replaces_that_list_as_a_whole(
    source: AsyncEngine,
) -> None:
    document = await export(source)
    loket = pool_of(document, "acme", "loket")
    loket["staffing_rules"] = []
    loket["timezone"] = "Europe/London"

    result = await service(source).apply(HOST, encode(document), None, None)

    assert result.plan.refusals == ()
    rules, overrides, zone = await rows(source, "acme", "loket")
    assert (rules, len(overrides), zone) == ([], 2, "Europe/London")


async def test_a_new_pool_arrives_with_its_staffing(target: AsyncEngine) -> None:
    document = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "name": "Acme",
                "capacity_pools": [
                    {
                        "code": "loket",
                        "name": "Loket",
                        "timezone": "Europe/Amsterdam",
                        "staffing_rules": [
                            {"weekday": 4, "opens": "08:30", "closes": "10:00", "units": 5}
                        ],
                        "staffing_overrides": [{"day": "2026-12-26"}],
                    }
                ],
            }
        ],
    }

    result = await service(target).apply(HOST, encode(document), None, None)

    assert result.plan.refusals == (), result.plan.refusals
    rules, overrides, zone = await rows(target, "acme", "loket")
    assert [(r.weekday, r.opens, r.closes, r.units, r.offer) for r in rules] == [
        (4, time(8, 30), time(10), 5, "scheduled")
    ]
    assert [(o.day, o.opens, o.units) for o in overrides] == [(date(2026, 12, 26), None, None)]
    assert zone == "Europe/Amsterdam"


async def test_lowering_the_units_below_a_booking_applies_and_keeps_the_booking(
    source: AsyncEngine,
) -> None:
    async with AsyncSession(source) as session, session.begin():
        loket = (
            await session.scalars(select(CapacityPoolRow).where(CapacityPoolRow.code == "bare"))
        ).one()
        group = await session.scalar(select(GroupRow).where(GroupRow.slug == "helpdesk"))
        assert group is not None
        slot = SlotRow(
            group_id=group.id,
            start=NOW,
            end=NOW,
            capacity_snapshot=1,
            capacity_pool_id=loket.id,
            occupied_until=NOW,
        )
        session.add(slot)
        await session.flush()
        session.add(
            BookingRow(
                slot_id=slot.id,
                group_id=group.id,
                status="confirmed",
                locale="nl",
                management_token_hash="a" * 64,
                public_ref="ref-pool",
                created_at=NOW,
            )
        )
    document = await export(source)
    pool_of(document, "acme", "loket")["staffing_rules"] = [
        {"weekday": 0, "opens": "09:00", "closes": "12:00", "units": 0}
    ]

    result = await service(source).apply(HOST, encode(document), None, None)

    assert result.plan.refusals == ()
    rules, _, _ = await rows(source, "acme", "loket")
    assert [r.units for r in rules] == [0]
    async with AsyncSession(source) as session:
        assert len((await session.scalars(select(BookingRow))).all()) == 1


async def test_a_document_reaches_the_pool_of_its_own_organisation_only(
    source: AsyncEngine,
) -> None:
    document = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "beta",
                "capacity_pools": [
                    {
                        "code": "loket",
                        "name": "Loket",
                        "timezone": "Europe/Paris",
                        "staffing_rules": [],
                    }
                ],
            }
        ],
    }
    acme_before = await rows(source, "acme", "loket")

    result = await service(source).apply(HOST, encode(document), None, None)

    assert result.plan.refusals == ()
    rules, _, zone = await rows(source, "beta", "loket")
    assert (rules, zone) == ([], "Europe/Paris")
    acme_after = await rows(source, "acme", "loket")
    assert [r.id for r in acme_after[0]] == [r.id for r in acme_before[0]]
    assert acme_after[2] == "Europe/Amsterdam"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"timezone": "Mars/Olympus"}, "timezone"),
        (
            {
                "staffing_rules": [
                    {"weekday": 0, "opens": "09:00", "closes": "12:00", "units": 1},
                    {"weekday": 0, "opens": "11:00", "closes": "13:00", "units": 1},
                ]
            },
            "staffing_rules",
        ),
    ],
)
async def test_a_refused_staffing_writes_nothing(
    source: AsyncEngine, changes: dict[str, Any], field: str
) -> None:
    document = await export(source)
    pool_of(document, "acme", "loket").update(changes)
    before = await rows(source, "acme", "loket")

    result = await service(source).apply(HOST, encode(document), None, None)

    assert not result.applied
    assert [(r.path.rsplit(".", 1)[-1], r.field) for r in result.plan.refusals] == [
        ("capacity_pools[loket]", field)
    ]
    after = await rows(source, "acme", "loket")
    assert [r.id for r in after[0]] == [r.id for r in before[0]]
    assert after[2] == before[2]


async def test_no_person_and_no_booking_is_in_the_staffing_of_the_document(
    source: AsyncEngine,
) -> None:
    text = json.dumps(pool_of(await export(source), "acme", "loket"))

    assert "booking" not in text
    assert "@" not in text
