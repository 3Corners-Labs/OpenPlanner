"""The agenda's live meeting status: polling as the fallback of webhooks (ADR 0010)."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import Booking, BookingStatus, Contact, MeetingRef
from app.domain.calls import (
    CallState,
    EventKind,
    MeetingStatus,
    apply_event,
    apply_polled,
    new_status,
    polled_events,
)
from app.domain.errors import DomainError
from app.domain.integration import IntegrationService
from app.domain.meeting_status_service import MAX_POLLS_PER_VIEW, MeetingStatusService
from app.domain.ports import IntegrationError
from app.domain.scheduling import PhoneMode
from tests.domain.test_integration import GROUP, GROUP_ADMIN, OUTSIDER
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeVideo
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import SLOT_0900

WINDOW = timedelta(seconds=30)
DAY = date(2026, 1, 5)
ADA = Contact.create(
    first_name="Ada",
    last_name="Lovelace",
    email="ada@example.org",
    phone=None,
    phone_mode=PhoneMode.OPTIONAL,
)
HOST_IN = MeetingStatus(
    CallState.IN_PROGRESS, participants=2, host_joined=True, started_at=SLOT_0900
)


def _booking(number: int = 1, **changes: object) -> Booking:
    booking = Booking(
        id=number,
        group_id=1,
        slot_id=number,
        start=SLOT_0900,
        end=SLOT_0900 + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        contact=ADA,
        locale="nl",
        token_hash=f"h{number}",
        created_at=SLOT_0900 - timedelta(days=1),
        meeting=MeetingRef(f"meeting-{number}", "https://site.webex.com/m"),
    )
    return replace(booking, **changes)  # type: ignore[arg-type]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(SLOT_0900 + timedelta(minutes=2))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.groups[1] = GROUP
    state.bookings[1] = _booking()
    state.call_status[1] = apply_event(
        new_status(1), EventKind.MEETING_CREATED, SLOT_0900 - timedelta(days=1)
    )
    return InMemoryUnitOfWork(state)


@pytest.fixture
def video() -> FakeVideo:
    return FakeVideo()


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock, video: FakeVideo) -> MeetingStatusService:
    integration = IntegrationService(uow, clock, AdminService(uow, clock), "1.2.3")
    return MeetingStatusService(uow, clock, integration, lambda: video, lambda: WINDOW, "1.2.3")


# --- the status a poll produces ----------------------------------------------------------


def test_a_poll_replaces_the_counts_a_lost_webhook_left_behind() -> None:
    before = apply_event(new_status(1), EventKind.MEETING_CREATED, SLOT_0900)

    after = apply_polled(before, HOST_IN, SLOT_0900 + timedelta(minutes=2))

    assert after.call is CallState.IN_PROGRESS
    assert (after.participants, after.host_joined) == (2, True)
    assert after.host_joined_at == SLOT_0900 + timedelta(minutes=2)
    assert after.started_at == SLOT_0900
    assert polled_events(before, after) == [EventKind.MEETING_STARTED, EventKind.HOST_JOINED]


def test_an_ended_meeting_stays_ended_and_a_host_who_joined_stays_joined() -> None:
    at = SLOT_0900 + timedelta(minutes=40)
    ended = apply_polled(
        apply_polled(new_status(1), HOST_IN, SLOT_0900),
        MeetingStatus(CallState.ENDED, ended_at=at),
        at,
    )

    assert (ended.call, ended.participants, ended.host_joined, ended.ended_at) == (
        CallState.ENDED,
        0,
        True,
        at,
    )
    again = apply_polled(ended, MeetingStatus(CallState.SCHEDULED), at + WINDOW)
    assert again.call is CallState.ENDED
    assert polled_events(ended, again) == []


def test_a_meeting_nobody_joined_ends_without_a_host() -> None:
    before = apply_event(new_status(1), EventKind.MEETING_CREATED, SLOT_0900)

    after = apply_polled(before, MeetingStatus(CallState.ENDED), SLOT_0900 + timedelta(hours=1))

    assert (after.call, after.host_joined) == (CallState.ENDED, False)
    assert after.ended_at == SLOT_0900 + timedelta(hours=1)
    assert polled_events(before, after) == [EventKind.MEETING_ENDED]


# --- when the provider is asked -----------------------------------------------------------


async def test_a_stale_status_is_refreshed_from_the_provider(
    service: MeetingStatusService, video: FakeVideo, uow: InMemoryUnitOfWork
) -> None:
    video.statuses["meeting-1"] = HOST_IN

    (item,) = await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert video.polled == [("meeting-1", "host-demo@example.nl")]
    assert (item.status.call, item.status.participants) == (CallState.IN_PROGRESS, 2)
    assert uow.state.call_status[1] == item.status
    events = uow.state.booking_events[1]
    assert [(e.kind, e.detail, e.version) for e in events] == [
        (EventKind.MEETING_STARTED, "polled", "1.2.3"),
        (EventKind.HOST_JOINED, "polled", "1.2.3"),
    ]


async def test_a_status_a_webhook_just_updated_is_not_asked(
    service: MeetingStatusService, video: FakeVideo, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.call_status[1] = apply_event(
        uow.state.call_status[1], EventKind.MEETING_STARTED, clock.now() - timedelta(seconds=10)
    )

    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert video.polled == []


async def test_a_booking_is_asked_once_per_window_however_often_the_page_asks(
    service: MeetingStatusService, video: FakeVideo, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    for _ in range(3):
        await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert len(video.polled) == 1

    uow.state.call_status[1] = replace(uow.state.call_status[1], updated_at=None)
    clock.advance(WINDOW)
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert len(video.polled) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"status": BookingStatus.CANCELLED},
        {"meeting": None},
        {"start": SLOT_0900 + timedelta(hours=2), "end": SLOT_0900 + timedelta(hours=3)},
        {"start": SLOT_0900 - timedelta(hours=3), "end": SLOT_0900 - timedelta(hours=2)},
    ],
    ids=["cancelled", "no-meeting", "far-ahead", "long-over"],
)
async def test_only_bookings_that_can_have_a_running_meeting_are_asked(
    service: MeetingStatusService,
    video: FakeVideo,
    uow: InMemoryUnitOfWork,
    change: dict[str, object],
) -> None:
    uow.state.bookings[1] = replace(uow.state.bookings[1], **change)  # type: ignore[arg-type]

    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert video.polled == []


async def test_an_ended_meeting_is_not_asked_again(
    service: MeetingStatusService, video: FakeVideo, uow: InMemoryUnitOfWork
) -> None:
    uow.state.call_status[1] = apply_event(
        uow.state.call_status[1], EventKind.MEETING_ENDED, SLOT_0900
    )

    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert video.polled == []


async def test_without_a_provider_or_host_the_overview_is_returned_as_it_is(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    none = MeetingStatusService(uow, clock, integration, lambda: None, lambda: WINDOW)
    assert len(await none.day_overview(GROUP_ADMIN, "demo", DAY)) == 1

    video = FakeVideo()
    uow.state.groups[1] = replace(GROUP, video_host_email=None)
    hostless = MeetingStatusService(uow, clock, integration, lambda: video, lambda: WINDOW)
    assert len(await hostless.day_overview(GROUP_ADMIN, "demo", DAY)) == 1
    assert video.polled == []


async def test_one_view_asks_for_at_most_the_ceiling(
    service: MeetingStatusService, video: FakeVideo, uow: InMemoryUnitOfWork
) -> None:
    for number in range(2, MAX_POLLS_PER_VIEW + 10):
        uow.state.bookings[number] = _booking(number)
        uow.state.call_status[number] = uow.state.call_status[1]

    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert len(video.polled) == MAX_POLLS_PER_VIEW

    await service.day_overview(GROUP_ADMIN, "demo", DAY)  # the rest on the next poll
    assert len(video.polled) == MAX_POLLS_PER_VIEW + 9


# --- failures ------------------------------------------------------------------------------


async def test_a_refusal_shows_the_providers_reason_on_the_timeline_once(
    service: MeetingStatusService,
    video: FakeVideo,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    video.status_error = IntegrationError(
        "rejected_403", retryable=False, reason="Host is not licensed"
    )

    (item,) = await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert item.status.call is CallState.SCHEDULED  # nothing is invented

    for _ in range(2):  # the window passes, the same refusal comes back
        clock.advance(WINDOW)
        await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert len(video.polled) == 3

    (event,) = uow.state.booking_events[1]
    assert event.kind is EventKind.INTEGRATION_ERROR
    assert (event.detail, event.message) == ("polled:rejected_403", "Host is not licensed")


async def test_a_failure_that_changes_is_recorded_again_and_a_recovery_clears_it(
    service: MeetingStatusService,
    video: FakeVideo,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    video.status_error = IntegrationError("unreachable", retryable=True)
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    clock.advance(WINDOW)
    video.status_error = IntegrationError("server_error", retryable=True)
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    clock.advance(WINDOW)
    video.status_error = None
    video.statuses["meeting-1"] = HOST_IN
    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert [e.detail for e in uow.state.booking_events[1]] == [
        "polled:unreachable",
        "polled:server_error",
        "polled",
        "polled",
    ]
    assert uow.state.call_status[1].call is CallState.IN_PROGRESS


# --- who may see it ------------------------------------------------------------------------


async def test_another_groups_admin_neither_sees_nor_triggers_a_refresh(
    service: MeetingStatusService, video: FakeVideo
) -> None:
    with pytest.raises(DomainError, match="not_permitted"):
        await service.day_overview(OUTSIDER, "demo", DAY)

    assert video.polled == []


async def test_a_remembered_failure_is_forgotten_when_the_join_window_closes(
    service: MeetingStatusService, video: FakeVideo, clock: FixedClock
) -> None:
    video.status_error = IntegrationError("unreachable", retryable=True)
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    assert service._failed  # noqa: SLF001 - the memory is the thing under test

    clock.advance(timedelta(hours=2))
    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert not service._failed  # noqa: SLF001
