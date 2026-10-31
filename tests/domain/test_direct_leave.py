"""A participant leaves a direct call from the waiting page (ADR 0019 point 4, ADR 0021)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import BookingKind, BookingStatus, Claim, MeetingRef
from app.domain.calls import CallState, CallStatus, EventKind, TaskState
from app.domain.direct_queue import DirectAvailability
from app.domain.errors import DomainError
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.job_plan import JobType
from app.domain.scheduling import Offer, ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import ADA, TEST_TOKEN_KEY, book, make_group, window

MONDAY_0915 = datetime(2026, 1, 5, 8, 15, tzinfo=UTC)  # 09:15 Amsterdam
GROUP = make_group(id=1, slug="aangifte", direct_call_max_concurrent=1)
RULES = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 1, Offer.BOTH))]


def _service() -> tuple[BookingService, InMemoryUnitOfWork]:
    state = State()
    state.groups[1] = GROUP
    state.rules[1] = list(RULES)
    uow = InMemoryUnitOfWork(state)
    return BookingService(uow, FixedClock(MONDAY_0915), TEST_TOKEN_KEY), uow


async def _started() -> tuple[BookingService, InMemoryUnitOfWork, str, int]:
    service, uow = _service()
    receipt = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    return service, uow, receipt.management_token, receipt.booking.id or 0


def _kinds(uow: InMemoryUnitOfWork, booking_id: int) -> list[EventKind]:
    return [e.kind for e in uow.state.booking_events.get(booking_id, [])]


async def test_leaving_a_waiting_call_cancels_it_and_ends_the_work_item() -> None:
    service, uow, token, booking_id = await _started()

    left = await service.leave_direct(token)

    assert left.changed
    assert not left.agent_present
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED
    jobs = [j.type for j in uow.state.jobs[booking_id]]
    assert jobs == [
        JobType.END_WXCC_TASK,
        JobType.DELETE_VIDEO_ROOM,
        JobType.DELETE_TRIAL_CALL,
        JobType.PURGE_PII,
    ]  # the pending dispatch and confirmation are gone; no mail to someone who left
    assert _kinds(uow, booking_id) == [EventKind.CITIZEN_LEFT]
    assert uow.state.booking_events[booking_id][0].detail == "waiting"


async def test_leaving_frees_the_place_under_the_cap() -> None:
    service, _, token, _ = await _started()
    assert await service.direct_call_availability("aangifte") is DirectAvailability.FULL

    await service.leave_direct(token)

    assert await service.direct_call_availability("aangifte") is DirectAvailability.AVAILABLE


@pytest.mark.parametrize("how", ["task", "claim", "host"])
async def test_leaving_after_an_agent_came_keeps_the_meeting_for_the_agent(how: str) -> None:
    """ADR 0019 point 4: the participant leaves; the agent is not cut off, the work item ends."""
    service, uow, token, booking_id = await _started()
    if how == "task":
        uow.state.call_status[booking_id] = CallStatus(booking_id, task=TaskState.CONNECTED)
    elif how == "host":
        uow.state.call_status[booking_id] = CallStatus(booking_id, host_joined=True)
    else:
        booking = uow.state.bookings[booking_id]
        uow.state.bookings[booking_id] = replace(booking, claim=Claim(MONDAY_0915, "pseudonym"))

    left = await service.leave_direct(token)

    assert left.changed
    assert left.agent_present
    jobs = [j.type for j in uow.state.jobs[booking_id]]
    assert JobType.END_WXCC_TASK in jobs
    assert JobType.DELETE_VIDEO_ROOM not in jobs
    assert uow.state.booking_events[booking_id][0].detail == "agent"
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED


async def test_leaving_twice_changes_nothing_the_second_time() -> None:
    service, uow, token, booking_id = await _started()
    await service.leave_direct(token)
    jobs, events = list(uow.state.jobs[booking_id]), _kinds(uow, booking_id)

    again = await service.leave_direct(token)

    assert not again.changed
    assert uow.state.jobs[booking_id] == jobs
    assert _kinds(uow, booking_id) == events


async def test_a_wrong_token_finds_nothing_and_changes_nothing() -> None:
    service, uow, _, booking_id = await _started()

    with pytest.raises(DomainError, match="booking_not_found"):
        await service.leave_direct("not-a-token")

    assert uow.state.bookings[booking_id].status is BookingStatus.CONFIRMED


async def test_a_token_cancels_only_its_own_call() -> None:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte")  # no cap: two calls at once
    state.rules[1] = list(RULES)
    uow = InMemoryUnitOfWork(state)
    service = BookingService(uow, FixedClock(MONDAY_0915), TEST_TOKEN_KEY)
    mine = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")
    theirs = await service.start_direct(group_slug="aangifte", contact=ADA, locale="nl")

    await service.leave_direct(mine.management_token)

    assert uow.state.bookings[mine.booking.id or 0].status is BookingStatus.CANCELLED
    assert uow.state.bookings[theirs.booking.id or 0].status is BookingStatus.CONFIRMED
    assert uow.state.booking_events.get(theirs.booking.id or 0) is None
    assert JobType.DISPATCH_WXCC_TASK in [j.type for j in uow.state.jobs[theirs.booking.id or 0]]


async def test_a_scheduled_booking_cannot_be_left_this_way() -> None:
    """An appointment's token is not a direct call's: it has its own cancel rules (ADR 0069)."""
    state = State()
    state.groups[1] = GROUP
    state.rules[1] = list(RULES)
    uow = InMemoryUnitOfWork(state)
    service = BookingService(uow, FixedClock(MONDAY_0915 - timedelta(days=2)), TEST_TOKEN_KEY)
    receipt = await book(service)
    assert receipt.booking.kind is BookingKind.SCHEDULED

    with pytest.raises(DomainError, match="booking_not_found"):
        await service.leave_direct(receipt.management_token)

    assert uow.state.bookings[receipt.booking.id or 0].status is BookingStatus.CONFIRMED


async def test_the_meeting_is_still_followed_after_the_citizen_left() -> None:
    """The agent stays in the meeting: Webex events keep reaching the timeline (ADR 0010)."""
    service, uow, token, booking_id = await _started()
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://site.webex.com/m/1"))
    uow.state.call_status[booking_id] = CallStatus(booking_id, host_joined=True)
    await service.leave_direct(token)
    integration = IntegrationService(
        uow, FixedClock(MONDAY_0915), AdminService(uow, FixedClock(MONDAY_0915))
    )

    outcome = await integration.record_meeting_event(
        "meeting-1", EventKind.MEETING_ENDED, MONDAY_0915 + timedelta(minutes=5)
    )

    assert outcome is EventOutcome.RECORDED
    assert _kinds(uow, booking_id) == [EventKind.CITIZEN_LEFT, EventKind.MEETING_ENDED]
    assert uow.state.call_status[booking_id].call is CallState.ENDED


async def test_an_agent_still_in_the_meeting_keeps_the_place_taken_until_it_ends() -> None:
    service, uow, token, booking_id = await _started()
    uow.state.call_status[booking_id] = CallStatus(booking_id, host_joined=True)
    await service.leave_direct(token)

    assert await service.direct_call_availability("aangifte") is DirectAvailability.FULL

    uow.state.call_status[booking_id] = CallStatus(
        booking_id, host_joined=True, call=CallState.ENDED
    )

    assert await service.direct_call_availability("aangifte") is DirectAvailability.AVAILABLE
