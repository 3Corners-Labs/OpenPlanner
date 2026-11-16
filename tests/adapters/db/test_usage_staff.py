"""A team member's usage facts against SQLite: written when on, rolled, read, folded (point 9)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import GroupRow, UsageEventRow, UsageStaffHourRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import Booking, BookingStatus, Claim
from app.domain.calls import EventKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.staff import StaffMember
from app.domain.staff_usage import StaffUsage
from app.domain.tokens import agent_pseudonym
from app.domain.usage import FactKind, UsageFact
from app.domain.usage_report_service import UsageReportService
from app.domain.usage_rollup_service import UsageRollup
from app.domain.usage_staff import StaffCell
from tests.fakes.clock import FixedClock
from tests.support.principals import principal
from tests.support.seed_rows import seed_group

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
START = datetime(2026, 11, 18, 10, tzinfo=UTC)
KEY = b"k" * 32
ORG_ADMIN = principal("o", "O", {Role.ORG_ADMIN}, organisations={"org-alfa"})
GROUP_ADMIN = principal("g", "G", {Role.GROUP_ADMIN}, groups=[("org-alfa", "alfa")])
AUDITOR = principal("a", "A", {Role.AUDITOR}, organisations={"org-alfa"})


def _staff(subject: str, name: str) -> StaffMember:
    return StaffMember(subject, name, frozenset(), frozenset(), NOW, NOW, NOW)


def _booking(group_id: int, subject: str | None) -> Booking:
    return Booking(
        id=5,
        group_id=group_id,
        slot_id=1,
        start=START,
        end=START + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        contact=None,
        locale="nl",
        token_hash="",
        created_at=START - timedelta(days=1),
        claim=Claim(START, agent_pseudonym(KEY, subject)) if subject else None,
    )


def _ended(group_id: int, organisation_id: int, seconds: int = 600) -> UsageFact:
    return UsageFact(FactKind.CALL_ENDED, START, organisation_id, group_id, value_seconds=seconds)


async def _world(engine: AsyncEngine, on: bool = True):  # type: ignore[no-untyped-def]
    await seed_group(engine, slug="alfa")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = (await _groups(engine))[0]
        loaded = await tx.get_group_by_id(group.id)
        assert loaded is not None
        await tx.save_staff(_staff("u1", "Olga"))
        await tx.save_staff(_staff("u2", "Bram"))
    return uow, loaded, StaffUsage([KEY], lambda _organisation: on)


async def _groups(engine: AsyncEngine) -> list[GroupRow]:
    async with AsyncSession(engine) as session:
        return list(await session.scalars(select(GroupRow)))


async def _count(engine: AsyncEngine, table: type) -> int:
    async with AsyncSession(engine) as session:
        return (await session.scalar(select(func.count()).select_from(table))) or 0


async def _id_of(uow: SqlUnitOfWork, subject: str) -> int:
    async with uow.read() as tx:
        member = await tx.get_staff(subject)
    assert member is not None
    assert member.id is not None
    return member.id


async def test_a_claim_is_a_fact_about_the_claimer_only_while_the_view_is_on(
    engine: AsyncEngine,
) -> None:
    uow, group, staff = await _world(engine)
    off = StaffUsage([KEY], lambda _organisation: False)

    async with uow.locked() as tx:
        await off.claimed(tx, "u1", group, NOW)
    assert await _count(engine, UsageEventRow) == 0

    async with uow.locked() as tx:
        await staff.claimed(tx, "u1", group, NOW)
    async with AsyncSession(engine) as session:
        [row] = list(await session.scalars(select(UsageEventRow)))
    assert (row.kind, row.staff_id) == ("queue.claimed", await _id_of(uow, "u1"))


async def test_a_person_the_directory_does_not_know_makes_no_fact(engine: AsyncEngine) -> None:
    uow, group, staff = await _world(engine)

    async with uow.locked() as tx:
        await staff.claimed(tx, "nobody", group, NOW)

    assert await _count(engine, UsageEventRow) == 0


async def test_the_facts_of_a_claimed_call_name_the_claimer(engine: AsyncEngine) -> None:
    uow, group, staff = await _world(engine)
    facts = [_ended(group.id, group.organisation_id, 900)]

    async with uow.locked() as tx:
        await staff.call(tx, _booking(group.id, "u2"), group, EventKind.MEETING_ENDED, facts)

    async with AsyncSession(engine) as session:
        [row] = list(
            await session.scalars(select(UsageEventRow).where(UsageEventRow.staff_id.is_not(None)))
        )
    assert (row.kind, row.staff_id, row.value_seconds) == (
        "call.ended",
        await _id_of(uow, "u2"),
        900,
    )


async def test_a_call_nobody_claimed_has_no_team_member(engine: AsyncEngine) -> None:
    uow, group, staff = await _world(engine)
    facts = [_ended(group.id, group.organisation_id)]

    async with uow.locked() as tx:
        await staff.call(tx, _booking(group.id, None), group, EventKind.MEETING_ENDED, facts)
        await staff.call(tx, _booking(group.id, "stranger"), group, EventKind.MEETING_ENDED, facts)

    assert await _count(engine, UsageEventRow) == 0


async def test_nothing_is_written_while_the_view_is_off(engine: AsyncEngine) -> None:
    uow, group, _ = await _world(engine)
    off = StaffUsage([KEY], lambda _organisation: False)
    facts = [_ended(group.id, group.organisation_id)]

    async with uow.locked() as tx:
        await off.call(tx, _booking(group.id, "u1"), group, EventKind.MEETING_ENDED, facts)

    assert await _count(engine, UsageEventRow) == 0


async def test_the_rollup_makes_team_cells_and_the_report_reads_them_by_name(
    engine: AsyncEngine,
) -> None:
    uow, group, staff = await _world(engine)
    clock = FixedClock(NOW)
    async with uow.locked() as tx:
        for _ in range(6):
            await staff.call(
                tx,
                _booking(group.id, "u1"),
                group,
                EventKind.MEETING_ENDED,
                [_ended(group.id, group.organisation_id, 600)],
            )
    await UsageRollup(
        uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25
    ).run_if_due()
    service = UsageReportService(
        uow, AdminService(uow, clock), clock, lambda: 5, lambda _o: 3, lambda _o: True
    )

    screen = await service.team(
        ORG_ADMIN,
        organisation_slug="org-alfa",
        first_day=date(2026, 11, 18),
        last_day=date(2026, 11, 18),
    )

    [row] = screen.report.rows
    assert (row.name, row.calls, row.call_minutes) == ("Olga", 6, 60)


async def test_the_team_report_is_refused_while_the_view_is_off(engine: AsyncEngine) -> None:
    uow, _, _ = await _world(engine)
    clock = FixedClock(NOW)
    service = UsageReportService(uow, AdminService(uow, clock), clock, lambda: 5, lambda _o: 3)

    with pytest.raises(DomainError) as refused:
        await service.team(ORG_ADMIN, organisation_slug="org-alfa")

    assert refused.value.code is ErrorCode.STAFF_VIEW_OFF


async def test_only_the_organisation_admin_may_read_the_team_report(engine: AsyncEngine) -> None:
    uow, _, _ = await _world(engine)
    clock = FixedClock(NOW)
    service = UsageReportService(
        uow, AdminService(uow, clock), clock, lambda: 5, lambda _o: 3, lambda _o: True
    )

    for denied in (GROUP_ADMIN, AUDITOR):
        with pytest.raises(DomainError) as refused:
            await service.team(denied, organisation_slug="org-alfa")
        assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_removing_a_person_folds_their_cells_and_waiting_facts_into_former_members(
    engine: AsyncEngine,
) -> None:
    uow, group, staff = await _world(engine)
    clock = FixedClock(NOW)
    rollup = UsageRollup(uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25)
    facts = [_ended(group.id, group.organisation_id, 600)]
    async with uow.locked() as tx:
        for _ in range(3):
            await staff.call(tx, _booking(group.id, "u1"), group, EventKind.MEETING_ENDED, facts)
    await rollup.run_if_due()  # three calls are rolled, two more only wait
    async with uow.locked() as tx:
        for _ in range(2):
            await staff.call(tx, _booking(group.id, "u1"), group, EventKind.MEETING_ENDED, facts)
    person = await _id_of(uow, "u1")

    async with uow.locked() as tx:
        await tx.erase_staff("u1", NOW)
    clock.advance(timedelta(minutes=20))
    await rollup.run_if_due()

    async with AsyncSession(engine) as session:
        rows = list(await session.scalars(select(UsageStaffHourRow)))
        left = await session.scalar(
            select(func.count()).select_from(UsageEventRow).where(UsageEventRow.staff_id == person)
        )
    assert {r.staff_id for r in rows} == {None}
    assert sum(r.count for r in rows if r.metric == "S1c") == 5
    assert sum(r.sum_value for r in rows if r.metric == "S1c") == 3000
    assert left == 0


async def test_retention_prunes_the_team_cells_with_the_hourly_rows(engine: AsyncEngine) -> None:
    uow, group, staff = await _world(engine)
    clock = FixedClock(NOW)
    old = datetime(2024, 1, 1, 10, tzinfo=UTC)
    async with uow.locked() as tx:
        await tx.add_staff_cells(
            [StaffCell(old, group.organisation_id, group.id, 1, "S1c", 1, 1, 1)]
        )
    rollup = UsageRollup(uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25)

    await rollup.run_if_due()

    assert await _count(engine, UsageStaffHourRow) == 0
