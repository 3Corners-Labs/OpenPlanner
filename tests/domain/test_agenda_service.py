from datetime import date, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.agenda_service import MAX_AGENDA_DAYS, AgendaService
from app.domain.authorization import Principal
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.errors import DomainError
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, SUPER
from tests.support.closures import NOTICES, three_groups
from tests.support.domain import (
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book,
    participant,
    window,
)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(three_groups())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def agenda(uow: InMemoryUnitOfWork, clock: FixedClock) -> AgendaService:
    return AgendaService(uow, clock, AdminService(uow, clock), horizon_days=14)


@pytest.fixture
def bookings(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    return BookingService(uow, clock, TEST_TOKEN_KEY)


def _lower_staffing(uow: InMemoryUnitOfWork, agents: int, group_id: int = 1) -> None:
    uow.state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=agents))]


async def test_the_week_shows_agents_and_bookings_per_slot(
    agenda: AgendaService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book(bookings, SLOT_0900, "demo")
    await book(bookings, SLOT_0900, "demo", participant(2))
    _lower_staffing(uow, 1)

    days = await agenda.days(GROUP_ADMIN, "demo", MONDAY, 7)

    assert [d.day for d in days] == [MONDAY + timedelta(days=n) for n in range(7)]
    monday = days[0].slots
    assert [(s.start, s.agents, s.booked, s.shortfall) for s in monday] == [
        (SLOT_0900, 1, 2, 1),
        (SLOT_0930, 1, 0, 0),
    ]
    assert days[1].slots == []


async def test_cancelled_bookings_need_no_agent(
    agenda: AgendaService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    await bookings.cancel(receipt.management_token)
    _lower_staffing(uow, 0)

    days = await agenda.days(GROUP_ADMIN, "demo", MONDAY, 1)

    assert days[0].slots == []


async def test_the_summary_counts_shortfalls_within_the_horizon(
    agenda: AgendaService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book(bookings, SLOT_0900, "demo")
    await book(bookings, SLOT_0930, "demo")
    await book(bookings, SLOT_0900 + timedelta(days=21), "demo")  # beyond 14 days
    _lower_staffing(uow, 0)

    summary = await agenda.shortfalls(GROUP_ADMIN, "demo")

    assert summary.count == 2
    assert summary.first is not None
    assert summary.first.start == SLOT_0900
    assert summary.horizon_days == 14


async def test_no_shortfall_means_no_first(agenda: AgendaService, bookings: BookingService) -> None:
    await book(bookings, SLOT_0900, "demo")

    summary = await agenda.shortfalls(GROUP_ADMIN, "demo")

    assert (summary.count, summary.first) == (0, None)


async def test_suspended_bookings_in_a_closure_need_no_agent(
    agenda: AgendaService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await book(bookings, SLOT_0900, "demo")
    closures = ClosureService(uow, clock, AdminService(uow, clock), bookings)
    await closures.close_group(SUPER, "demo", ClosureInput(None, None, NOTICES))

    days = await agenda.days(SUPER, "demo", MONDAY, 1)

    assert [(s.agents, s.booked, s.closed) for s in days[0].slots] == [(0, 0, True)] * 2


async def test_summaries_cover_every_group_in_scope(
    agenda: AgendaService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book(bookings, SLOT_0900, "andere-team")
    _lower_staffing(uow, 0, group_id=2)

    assert {k: v.count for k, v in (await agenda.summaries(SUPER)).items()} == {
        "demo": 0,
        "demo-twee": 0,
        "andere-team": 1,
    }
    assert list(await agenda.summaries(GROUP_ADMIN)) == ["demo"]
    assert await agenda.summaries(AUDITOR) == {}  # no bookings.view: no agenda at all


@pytest.mark.parametrize("principal", [AUDITOR, GROUP_ADMIN])
async def test_another_groups_agenda_is_refused(
    agenda: AgendaService, principal: Principal
) -> None:
    slug = "demo" if principal is AUDITOR else "andere-team"
    with pytest.raises(DomainError, match="not_permitted"):
        await agenda.days(principal, slug, MONDAY, 1)
    with pytest.raises(DomainError, match="not_permitted"):
        await agenda.shortfalls(principal, slug)


@pytest.mark.parametrize("days", [0, MAX_AGENDA_DAYS + 1])
async def test_the_range_is_bounded(agenda: AgendaService, days: int) -> None:
    with pytest.raises(DomainError, match="invalid_range"):
        await agenda.days(SUPER, "demo", date(2026, 1, 5), days)


async def test_counts_today_counts_confirmed_bookings_per_group_in_scope(
    agenda: AgendaService, bookings: BookingService, clock: FixedClock
) -> None:
    """ADR 0061: the overview widget shows counts only, for the groups one may view."""
    await book(bookings, SLOT_0900, "demo")
    await book(bookings, SLOT_0930, "demo", participant(2))
    receipt = await book(bookings, SLOT_0900, "demo", participant(3))
    await bookings.cancel(receipt.management_token)

    assert await agenda.counts_today(SUPER) == {"demo": 0, "demo-twee": 0, "andere-team": 0}
    clock.advance(SLOT_0900 - NOW - timedelta(hours=2))
    assert await agenda.counts_today(SUPER) == {"demo": 2, "demo-twee": 0, "andere-team": 0}
    assert await agenda.counts_today(GROUP_ADMIN) == {"demo": 2}
    assert await agenda.counts_today(AUDITOR) == {}
