from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role
from app.domain.booking import Booking, BookingStatus, Contact, MeetingRef
from app.domain.calls import (
    CallState,
    EventKind,
    TaskState,
    apply_event,
    meeting_request,
    new_status,
    not_picked_up,
    work_item,
)
from app.domain.errors import DomainError
from app.domain.integration import IntegrationService
from app.domain.scheduling import PhoneMode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, SLOT_0900, make_group

GROUP = make_group(
    id=1,
    organisation_id=1,
    slug="demo",
    name="Demo: videogesprek",
    video_host_email="host-demo@example.nl",
    wxcc_business_address="openplanner@example.biz",
    wxcc_channel="openplanner-video",
)
ADA = Contact.create(
    name="Ada Lovelace", email="ada@example.org", phone=None, phone_mode=PhoneMode.OPTIONAL
)
GROUP_ADMIN = Principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OUTSIDER = Principal("x-1", "X", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset())


def _booking(**changes: object) -> Booking:
    booking = Booking(
        id=123,
        group_id=1,
        slot_id=7,
        start=SLOT_0900,
        end=SLOT_0900 + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        contact=ADA,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )
    return replace(booking, **changes)  # type: ignore[arg-type]


# --- what leaves OpenPlanner -------------------------------------------------------------


def test_meeting_request_carries_no_personal_data() -> None:
    request = meeting_request(_booking(), GROUP)

    assert request.title == "Videogesprek Demo: videogesprek"
    assert (request.start, request.end) == (SLOT_0900, SLOT_0900 + timedelta(minutes=30))
    assert request.host_email == "host-demo@example.nl"
    assert request.timezone == "Europe/Amsterdam"
    assert "Ada" not in repr(request)


def test_work_item_carries_only_first_name_and_meeting_link() -> None:
    booking = _booking(meeting=MeetingRef("m-1", "https://site.webex.com/meet/abc"))

    item = work_item(booking, GROUP, start_label="5 jan 2026, 09:00")

    assert item.reference == "OP-000123"
    assert item.first_name == "Ada"
    assert item.meeting_link == "https://site.webex.com/meet/abc"
    assert (item.business_address, item.channel, item.routing_value) == (
        "openplanner@example.biz",
        "openplanner-video",
        "demo",
    )
    assert item.kind == "scheduled"
    assert "Lovelace" not in repr(item)
    assert "ada@example.org" not in repr(item)


def test_work_item_needs_a_meeting_and_contact_data() -> None:
    with pytest.raises(DomainError, match="integration_not_ready: meeting"):
        work_item(_booking(), GROUP, start_label="x")
    with pytest.raises(DomainError, match="integration_not_ready: contact"):
        work_item(
            _booking(meeting=MeetingRef("m", "https://x.webex.com/m"), contact=None),
            GROUP,
            start_label="x",
        )


# --- call status -------------------------------------------------------------------------


def test_status_follows_task_and_meeting_events() -> None:
    status = new_status(123)
    t = NOW
    status = apply_event(status, EventKind.TASK_CREATED, t)
    status = apply_event(status, EventKind.TASK_CONNECTED, t + timedelta(minutes=1))
    status = apply_event(status, EventKind.MEETING_STARTED, t + timedelta(minutes=2))
    status = apply_event(status, EventKind.HOST_JOINED, t + timedelta(minutes=2))
    status = apply_event(status, EventKind.GUEST_JOINED, t + timedelta(minutes=3))
    status = apply_event(status, EventKind.GUEST_JOINED, t + timedelta(minutes=4))

    assert status.task is TaskState.CONNECTED
    assert status.call is CallState.IN_PROGRESS
    assert (status.participants, status.host_joined) == (3, True)
    assert status.host_joined_at == t + timedelta(minutes=2)

    status = apply_event(status, EventKind.PARTICIPANT_LEFT, t + timedelta(minutes=20))
    status = apply_event(status, EventKind.MEETING_ENDED, t + timedelta(minutes=30))
    status = apply_event(status, EventKind.TASK_ENDED, t + timedelta(minutes=31))

    assert status.call is CallState.ENDED
    assert status.participants == 0
    assert status.ended_at == t + timedelta(minutes=30)
    assert status.task is TaskState.ENDED


def test_participants_never_go_below_zero_and_failures_are_kept() -> None:
    status = apply_event(new_status(1), EventKind.PARTICIPANT_LEFT, NOW)
    failed = apply_event(new_status(1), EventKind.TASK_FAILED, NOW)

    assert status.participants == 0
    assert failed.task is TaskState.FAILED


def test_not_picked_up_after_the_group_limit_without_host() -> None:
    queued = apply_event(new_status(1), EventKind.TASK_CREATED, NOW)
    joined = apply_event(queued, EventKind.HOST_JOINED, NOW)
    limit = timedelta(minutes=10)

    assert not not_picked_up(queued, SLOT_0900, SLOT_0900 + timedelta(minutes=9), limit)
    assert not_picked_up(queued, SLOT_0900, SLOT_0900 + timedelta(minutes=11), limit)
    assert not not_picked_up(joined, SLOT_0900, SLOT_0900 + timedelta(minutes=11), limit)
    assert not not_picked_up(new_status(1), SLOT_0900, SLOT_0900 + timedelta(hours=1), limit)


# --- recording events --------------------------------------------------------------------


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.groups[1] = GROUP
    state.bookings[123] = _booking(
        meeting=MeetingRef("meeting-1", "https://site.webex.com/m"), wxcc_task_id="task-1"
    )
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> IntegrationService:
    clock = FixedClock(NOW)
    return IntegrationService(uow, clock, AdminService(uow, clock))


async def test_events_are_correlated_by_meeting_and_task(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.record_meeting_event("meeting-1_I_42", EventKind.MEETING_STARTED, NOW)
    assert await service.record_task_event("task-1", EventKind.TASK_CONNECTED, NOW)
    assert not await service.record_meeting_event("unknown", EventKind.MEETING_STARTED, NOW)
    assert not await service.record_task_event("unknown", EventKind.TASK_ENDED, NOW)

    assert [e.kind for e in uow.state.booking_events[123]] == [
        EventKind.MEETING_STARTED,
        EventKind.TASK_CONNECTED,
    ]
    assert uow.state.call_status[123].call is CallState.IN_PROGRESS
    assert uow.state.call_status[123].task is TaskState.CONNECTED


async def test_failure_reasons_are_kept_as_detail(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    await service.record_task_event("task-1", EventKind.TASK_FAILED, NOW, "ENTRY_POINT_NOT_FOUND")

    assert uow.state.booking_events[123][0].detail == "ENTRY_POINT_NOT_FOUND"


async def test_timeline_needs_bookings_view_on_the_group(service: IntegrationService) -> None:
    await service.record(123, EventKind.MEETING_CREATED)

    timeline = await service.timeline(GROUP_ADMIN, 123)

    assert [e.kind for e in timeline.events] == [EventKind.MEETING_CREATED]
    assert timeline.booking.id == 123
    with pytest.raises(DomainError, match="not_permitted"):
        await service.timeline(OUTSIDER, 123)
    with pytest.raises(DomainError, match="booking_not_found"):
        await service.timeline(GROUP_ADMIN, 999)


def test_join_window_opens_fifteen_minutes_before_and_closes_after_the_end() -> None:
    from app.domain.calls import JoinState, join_state

    booking = _booking(meeting=MeetingRef("m", "https://site.webex.com/m"))
    start, end = booking.start, booking.end

    assert join_state(booking, start - timedelta(minutes=16)) is JoinState.TOO_EARLY
    assert join_state(booking, start - timedelta(minutes=15)) is JoinState.OPEN
    assert join_state(booking, end + timedelta(minutes=30)) is JoinState.OPEN
    assert join_state(booking, end + timedelta(minutes=31)) is JoinState.CLOSED
    assert join_state(replace(booking, meeting=None), start) is JoinState.NO_MEETING
    cancelled = replace(booking, status=BookingStatus.CANCELLED)
    assert join_state(cancelled, start) is JoinState.INACTIVE
