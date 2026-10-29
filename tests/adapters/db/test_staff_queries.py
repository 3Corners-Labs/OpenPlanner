"""The staff directory's queries in SQLite (ADR 0046): the same rules as the domain."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import StaffRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Role, Scope
from app.domain.staff import Membership, StaffFilter, StaffMember, visible
from tests.support.domain import NOW

DEMO, DEMO_TEAM, OTHER = Membership("demo"), Membership("demo", "team"), Membership("andere")

PEOPLE = (
    StaffMember(
        "sub-org",
        "Olaf",
        frozenset({Role.ORG_ADMIN}),
        frozenset({DEMO}),
        NOW,
        NOW - timedelta(days=100),
        NOW,
    ),
    StaffMember(
        "sub-group", "Gina", frozenset({Role.GROUP_ADMIN}), frozenset({DEMO_TEAM}), NOW, NOW, NOW
    ),
    StaffMember(
        "sub-two",
        "Tim",
        frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
        frozenset({OTHER, DEMO_TEAM}),
        NOW,
        NOW,
        NOW,
    ),
    StaffMember(
        "sub-super", "Sanne", frozenset({Role.SUPERUSER}), frozenset({DEMO}), NOW, NOW, NOW
    ),
    StaffMember("sub-none", "Nina", frozenset(), frozenset(), NOW, NOW, NOW),
)

SCOPES = (
    Scope(everything=True),
    Scope(organisations=frozenset({"demo"})),
    Scope(organisations=frozenset({"andere"})),
    Scope(groups=frozenset({"team"})),
    Scope(),
)

FILTERS = (
    StaffFilter(),
    StaffFilter(organisation="andere"),
    StaffFilter(group="team"),
    StaffFilter(role=Role.GROUP_ADMIN),
    StaffFilter(role=Role.SUPERUSER),
    StaffFilter(dormant_before=NOW - timedelta(days=90)),
)


async def _people(engine: AsyncEngine) -> SqlUnitOfWork:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        for person in PEOPLE:
            await tx.save_staff(person)
    return uow


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("narrow", FILTERS)
async def test_sql_applies_the_domain_rules(
    engine: AsyncEngine, scope: Scope, narrow: StaffFilter
) -> None:
    uow = await _people(engine)

    async with uow.read() as tx:
        found = await tx.list_staff(scope, narrow)

    expected = sorted(p.display_name for p in PEOPLE if visible(p, scope) and narrow.matches(p))
    assert [m.display_name for m in found] == expected


async def test_a_record_round_trips_and_its_memberships_are_replaced(engine: AsyncEngine) -> None:
    uow = await _people(engine)
    moved = replace(PEOPLE[2], memberships=frozenset({DEMO}), last_login_at=NOW + timedelta(1))

    async with uow.locked() as tx:
        before = await tx.get_staff("sub-two")
        await tx.save_staff(moved)
    async with uow.read() as tx:
        after = await tx.get_staff("sub-two")
        by_id = await tx.list_staff(
            Scope(everything=True), StaffFilter(id=after.id if after else 0)
        )

    assert before == replace(PEOPLE[2], id=before.id if before else None)
    assert after == replace(moved, id=before.id if before else None)
    assert by_id == [after]


async def test_seen_and_logout_are_recorded_for_known_subjects_only(engine: AsyncEngine) -> None:
    uow = await _people(engine)
    later = NOW + timedelta(minutes=10)

    async with uow.locked() as tx:
        await tx.set_staff_seen("sub-org", later)
        await tx.set_staff_logout("sub-org", later)
        await tx.set_staff_seen("sub-unknown", later)
        await tx.set_staff_logout("sub-unknown", later)
    async with uow.read() as tx:
        olaf = await tx.get_staff("sub-org")
        unknown = await tx.get_staff("sub-unknown")

    assert olaf is not None
    assert (olaf.last_seen_at, olaf.last_logout_at) == (later, later)
    assert unknown is None


async def test_a_carried_over_profile_is_not_recorded_yet(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        session.add(
            StaffRow(
                subject="sub-old",
                display_name="Oud",
                roles=None,
                first_seen_at=NOW,
                last_login_at=NOW,
                last_seen_at=NOW,
            )
        )

    async with SqlUnitOfWork(engine).read() as tx:
        old = await tx.get_staff("sub-old")

    assert old is not None
    assert not old.recorded
    assert old.roles == frozenset()
