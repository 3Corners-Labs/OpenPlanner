"""The staff directory's queries in SQLite (ADR 0046): the same rules as the domain."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import StaffRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Role, Scope
from app.domain.staff import Membership, RefusedEntitlement, StaffFilter, StaffMember, visible
from tests.support.domain import NOW

DEMO = Membership("org_admin", "demo")
DEMO_TEAM = Membership("group_admin", "demo", "team")
OTHER = Membership("org_admin", "andere")

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
    Scope(groups=frozenset({("demo", "team")})),
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


async def test_refusals_round_trip_and_feed_the_filter_and_the_mapping(engine: AsyncEngine) -> None:
    # ADR 0049 points 8 and 17.
    uow = await _people(engine)
    refused = (
        RefusedEntitlement("openplanner:org_admin:gem-x", "unknown_organisation", None, NOW),
        RefusedEntitlement("openplanner:agent:demo/nope", "unknown_group", "demo", NOW),
    )
    async with uow.locked() as tx:
        await tx.save_staff(replace(PEOPLE[0], refused=refused))
    async with uow.read() as tx:
        olaf = await tx.get_staff("sub-org")
        flagged = await tx.list_staff(Scope(everything=True), StaffFilter(unrecognised=True))
        for_demo = await tx.list_refused_entitlements(
            Scope(organisations=frozenset({"demo"})), NOW - timedelta(days=1)
        )
        for_all = await tx.list_refused_entitlements(
            Scope(everything=True), NOW - timedelta(days=1)
        )
        stale = await tx.list_refused_entitlements(Scope(everything=True), NOW + timedelta(days=1))
        holders = await tx.list_grant_holders(Scope(organisations=frozenset({"demo"})))
        directory = await tx.get_directory()

    assert olaf is not None
    assert olaf.refused == tuple(sorted(refused, key=lambda r: r.value))
    assert [m.subject for m in flagged] == ["sub-org"]
    assert [r.value for r in for_demo] == ["openplanner:agent:demo/nope"]
    assert len(for_all) == 2
    assert stale == []
    assert [(h.membership, h.holders) for h in holders] == [
        (DEMO, 2),
        (DEMO_TEAM, 2),
    ]
    assert directory.organisations == ()
    assert directory.groups == ()
    assert directory.roles["org_admin"].value == "organisations"


async def test_the_directory_carries_the_idp_values_of_organisations_and_groups(
    engine: AsyncEngine,
) -> None:
    from app.adapters.db.seed import seed_demo

    await seed_demo(engine)
    async with SqlUnitOfWork(engine).read() as tx:
        directory = await tx.get_directory()

    assert directory.organisation_by_idp_key("demo") is not None
    assert directory.organisation_by_idp_key("demo").slug == "demo"  # type: ignore[union-attr]
    demo = directory.group_by_slug("demo")
    assert demo is not None
    assert (demo.idp_key, demo.organisation) == ("demo", "demo")
    assert directory.groups_by_idp_key("andere-team")[0].organisation == "andere"


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
