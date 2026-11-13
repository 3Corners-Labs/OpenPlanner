"""Immediate intake and immediate work on the same pool as appointments (ADR 0121 step 7).

The brief's example runs end to end (ADR 0122 section 2.4): two units, two appointments at
10:00, a request without appointment at 09:55. It is taken in with an expected start of 10:30,
its claim at 09:55 is refused, at 10:30 it is allowed, and once claimed it holds a unit that an
appointment admission and another claim both see. The gate is opened for every test here.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.closure import Closure
from app.domain.errors import DomainError, ErrorCode
from app.domain.immediate_intake import RequestStatus
from app.domain.retention_sweep import RetentionSweep
from app.domain.scheduling import Offer
from app.domain.service_requests import (
    HandlingState,
    PersonalDataState,
    WorkItemState,
)
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import TEST_TOKEN_KEY
from tests.support.immediate import (
    AGENT,
    GUEST,
    NOW_FLOW,
    POOL,
    appointment,
    ask,
    at,
    intake,
    staff,
    watch,
    world,
)
from tests.support.pool_flows import open_gate


@pytest.fixture(autouse=True)
def _gate(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return world()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(at(9, 55))


async def two_reservations(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    """R1 and R2: appointments of 30 minutes at 10:00, both units of the pool."""
    for _ in range(2):
        await appointment(uow, clock, at(10), NOW_FLOW, 30)


# --- intake ------------------------------------------------------------------------------------


async def test_a_request_is_taken_in_with_its_record_and_the_forecast(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A17: waiting is allowed before reserved units; the forecast is 10:30."""
    await two_reservations(uow, clock)

    accepted = await ask(uow, clock)

    request = accepted.request
    assert accepted.created
    assert accepted.expected_start == at(10, 30)
    assert request.handling is HandlingState.QUEUED
    assert request.pool_id == POOL
    assert request.arrived_at == at(9, 55)
    assert request.watchdog_at == at(9, 55) + timedelta(hours=24)
    assert request.personal_data is PersonalDataState.PRESENT
    record = uow.state.request_data[request.id or 0]
    assert (record.first_name, record.email) == ("Ada", "ada@voorbeeld.nl")
    assert accepted.cookie_value not in repr(request)


async def test_a_retried_intake_returns_the_same_request(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A16: the same form key, sent again after a lost answer, makes no second request."""
    first = await ask(uow, clock)
    again = await ask(uow, clock)

    assert not again.created
    assert again.request.id == first.request.id
    assert len(uow.state.requests) == 1
    page = await intake(uow, clock).page(first.request.public_ref or "", again.cookie_value)
    assert page.status is RequestStatus.WAITING


async def test_an_old_form_sent_again_after_the_request_ended_takes_over_nothing(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    first = await ask(uow, clock)
    await intake(uow, clock).withdraw(first.request.public_ref or "", first.cookie_value)

    with pytest.raises(DomainError) as error:
        await ask(uow, clock)
    assert error.value.field == "intake_key"
    page = await intake(uow, clock).page(first.request.public_ref or "", first.cookie_value)
    assert page.status is RequestStatus.WITHDRAWN  # the first browser keeps its page


async def test_a_flow_that_does_not_collect_email_stores_none(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A20: nothing is invented for a field that was not asked."""
    revision = uow.state.flow_revisions[(60, 1)]
    uow.state.flow_revisions[(60, 1)] = replace(revision, collects_email=False)
    flow = uow.state.flows[60]
    uow.state.flows[60] = replace(flow, current=uow.state.flow_revisions[(60, 1)])

    accepted = await ask(uow, clock)

    record = uow.state.request_data[accepted.request.id or 0]
    assert record.email is None
    assert record.first_name == "Ada"


async def test_the_waiting_limit_and_the_look_ahead_refuse(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.pool_max_waiting[POOL] = 1
    await ask(uow, clock)
    with pytest.raises(DomainError) as error:
        await ask(uow, clock, key="m" * 43)
    assert error.value.code is ErrorCode.ON_DEMAND_FULL

    clock.advance(at(11, 50) - clock.now())  # 20 minutes no longer fit before 12:00
    uow.state.pool_max_waiting.clear()
    with pytest.raises(DomainError) as error:
        await ask(uow, clock, key="n" * 43)
    assert error.value.code is ErrorCode.ON_DEMAND_UNAVAILABLE


async def test_a_pool_block_for_appointments_only_takes_no_immediate_work(
    clock: FixedClock,
) -> None:
    """P7: the block offers appointments only."""
    with pytest.raises(DomainError) as error:
        await ask(world(offer=Offer.SCHEDULED), clock)
    assert error.value.code is ErrorCode.ON_DEMAND_UNAVAILABLE


async def test_a_bad_intake_key_and_a_bad_contact_are_refused_before_anything_is_stored(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError) as error:
        await ask(uow, clock, key="short")
    assert error.value.field == "intake_key"
    with pytest.raises(DomainError) as error:
        await ask(uow, clock, contact=replace(GUEST, first_name=""))
    assert error.value.code is ErrorCode.INVALID_CONTACT
    assert not uow.state.requests


async def test_a_closed_service_takes_nothing_in(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.closures[1] = Closure(1, 1, at(9), None, {"nl": "Dicht", "en": "Closed"}, at(9))
    with pytest.raises(DomainError) as error:
        await ask(uow, clock)
    assert error.value.code is ErrorCode.GROUP_CLOSED


async def test_the_legacy_direct_call_never_takes_a_pool_flow(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The on-demand flow is on a pool: the direct call refuses it, immediate intake takes it."""
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    with pytest.raises(DomainError) as error:
        await service.start_direct(group_slug="demo", contact=GUEST, locale="nl")
    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE
    assert not uow.state.bookings


async def test_a_service_without_a_pool_flow_offers_the_direct_call(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    del uow.state.flows[60]
    assert await intake(uow, clock).offer("demo") is None
    with pytest.raises(DomainError) as error:
        await ask(uow, clock)
    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE


# --- the participant's page ------------------------------------------------------------------


async def test_the_page_needs_its_cookie_and_says_the_place(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A31: a wrong cookie and an unknown reference get the same answer."""
    first = await ask(uow, clock)
    second = await ask(uow, clock, key="m" * 43)
    service = intake(uow, clock)

    page = await service.page(second.request.public_ref or "", second.cookie_value)
    assert (page.status, page.place, page.first_name) == (RequestStatus.WAITING, 2, "Ada")
    assert page.expected_start == at(9, 55)
    for ref, value in (
        (second.request.public_ref or "", first.cookie_value),
        ("unknown", second.cookie_value),
        (second.request.public_ref or "", None),
    ):
        with pytest.raises(DomainError) as error:
            await service.page(ref, value)
        assert error.value.code is ErrorCode.LINK_REQUIRED


async def test_withdrawing_a_claimed_request_releases_its_unit_once(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """I7: until treatment starts; a second withdrawal changes nothing."""
    accepted = await ask(uow, clock)
    await staff(uow, clock).claim(AGENT, "demo")
    assert await _held(uow) == 1

    service = intake(uow, clock)
    page = await service.withdraw(accepted.request.public_ref or "", accepted.cookie_value)
    again = await service.withdraw(accepted.request.public_ref or "", accepted.cookie_value)

    assert page.status is RequestStatus.WITHDRAWN
    assert again.status is RequestStatus.WITHDRAWN
    assert await _held(uow) == 0
    states = [i.state for i in uow.state.work_items.values()]
    assert states == [WorkItemState.CANCELLED]


# --- retention: the first writer of request-level data ---------------------------------------


async def test_an_immediate_record_is_deleted_end_to_end(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A33: never claimed, it expires at its watchdog and its record goes 24 hours later."""
    accepted = await ask(uow, clock)
    request_id = accepted.request.id or 0

    clock.advance(timedelta(hours=24))
    await watch(uow, clock).run()
    assert uow.state.requests[request_id].handling is HandlingState.EXPIRED
    assert request_id in uow.state.request_data

    clock.advance(timedelta(hours=24, minutes=1))
    assert (await watch(uow, clock).run()).deleted == 1
    request = uow.state.requests[request_id]
    assert request_id not in uow.state.request_data
    assert request.personal_data is PersonalDataState.DELETED
    assert request.access_hash is None
    assert request.intake_key_hash is None
    with pytest.raises(DomainError) as error:
        await intake(uow, clock).page(request.public_ref or "", accepted.cookie_value)
    assert error.value.code is ErrorCode.LINK_REQUIRED


async def test_the_nightly_sweep_counts_and_deletes_a_straggling_record(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The sweep's overdue count and its 48-hour backlog alert count request-level records."""
    accepted = await ask(uow, clock)
    request_id = accepted.request.id or 0
    clock.advance(timedelta(hours=24))
    await watch(uow, clock).run()  # expired; deletion due at +48 h, but the round is missed
    sweep = RetentionSweep(uow, clock)

    clock.advance(timedelta(hours=25, minutes=30))
    assert (await sweep.run(dry_run=True)).overdue == 1
    clock.advance(timedelta(hours=24))
    found = await sweep.run(dry_run=True)
    assert (found.overdue, found.backlog) == (1, 1)

    done = await sweep.run()
    assert done.purged == 1
    assert request_id not in uow.state.request_data


async def _held(uow: InMemoryUnitOfWork) -> int:
    """Return how many units of the pool immediate work holds now (claimed or started)."""
    return sum(
        1
        for item in uow.state.work_items.values()
        if item.pool_id == POOL and item.ended_at is None
    )
