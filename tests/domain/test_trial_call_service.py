"""Opening, joining and deleting a trial call (ADR 0069)."""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import BookingStatus
from app.domain.calls import EventKind
from app.domain.errors import DomainError
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.ports import IntegrationError
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.trial_call import TrialState
from app.domain.trial_call_service import TrialCallService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeVideo
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book, make_group, window

HOST = "host@example.nl"


class Setup:
    """A group with video, a booking at Monday 09:00 and the trial call service."""

    def __init__(self, **group: object) -> None:
        state = State()
        state.groups[1] = make_group(id=1, slug="aangifte", video_host_email=HOST, **group)
        state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
        self.uow = InMemoryUnitOfWork(state)
        self.clock = FixedClock(NOW)
        self.video = FakeVideo()
        integration = IntegrationService(self.uow, self.clock, AdminService(self.uow, self.clock))
        self.service = TrialCallService(self.uow, self.video, integration, self.clock)
        self.token = ""
        self.booking_id = 0

    async def booked(self) -> "Setup":
        receipt = await book(BookingService(self.uow, self.clock, TEST_TOKEN_KEY))
        self.token = receipt.management_token
        self.booking_id = receipt.booking.id or 0
        return self

    def events(self) -> list[EventKind]:
        return [e.kind for e in self.uow.state.booking_events.get(self.booking_id, [])]

    def jobs(self) -> list[JobType]:
        return [j.type for j in self.uow.state.jobs[self.booking_id]]


@pytest.fixture
async def setup() -> Setup:
    return await Setup().booked()


async def test_opening_schedules_a_meeting_the_citizen_can_enter_alone(setup: Setup) -> None:
    view = await setup.service.open(setup.token)

    assert view.state is TrialState.ACTIVE
    assert view.expires_at == NOW + timedelta(minutes=30)
    assert view.calls_left == 2
    request = setup.video.created[0]
    assert (request.title, request.host_email) == ("Proefgesprek Aangifte", HOST)
    assert request.join_before_host is True
    stored = setup.uow.state.bookings[setup.booking_id]
    assert stored.trial is not None
    assert stored.trial.meeting.id == "meeting-1"
    assert stored.trial_count == 1
    assert setup.events() == [EventKind.TRIAL_CREATED]


async def test_a_delete_job_is_planned_after_the_trial_call_closes(setup: Setup) -> None:
    await setup.service.open(setup.token)

    deletes = [
        j for j in setup.uow.state.jobs[setup.booking_id] if j.type is JobType.DELETE_TRIAL_CALL
    ]
    assert [j.run_at for j in deletes] == [NOW + timedelta(minutes=35)]
    assert deletes[0].pinned_start is None  # does nothing about the start: it only cleans up


async def test_opening_again_reuses_the_open_trial_call(setup: Setup) -> None:
    await setup.service.open(setup.token)
    again = await setup.service.open(setup.token)

    assert again.state is TrialState.ACTIVE
    assert len(setup.video.created) == 1
    assert setup.uow.state.bookings[setup.booking_id].trial_count == 1


async def test_the_link_is_followed_only_while_the_trial_call_is_open(setup: Setup) -> None:
    await setup.service.open(setup.token)

    assert await setup.service.join_link(setup.token) == "https://site.webex.com/m/1"
    setup.clock.advance(timedelta(minutes=31))
    assert await setup.service.join_link(setup.token) is None
    assert setup.events() == [EventKind.TRIAL_CREATED, EventKind.TRIAL_JOINED]


async def test_a_closed_trial_call_can_be_opened_again_until_the_limit(setup: Setup) -> None:
    for _ in range(3):
        await setup.service.open(setup.token)
        setup.clock.advance(timedelta(minutes=31))

    view = await setup.service.open(setup.token)

    assert view.state is TrialState.LIMIT_REACHED
    assert len(setup.video.created) == 3
    assert view.calls_left == 0


async def test_a_refusal_by_webex_is_recorded_with_its_reason_and_does_not_count(
    setup: Setup,
) -> None:
    setup.video.error = IntegrationError(
        "rejected_400", retryable=False, reason="no join before host"
    )

    view = await setup.service.open(setup.token)

    assert view.failed is True
    assert view.state is TrialState.AVAILABLE
    assert setup.uow.state.bookings[setup.booking_id].trial_count == 0
    event = setup.uow.state.booking_events[setup.booking_id][0]
    assert (event.kind, event.detail, event.message) == (
        EventKind.TRIAL_FAILED,
        "rejected_400",
        "no join before host",
    )


async def test_without_video_or_with_the_switch_off_nothing_is_offered() -> None:
    off = await Setup(trial_call_enabled=False).booked()
    no_video = await Setup().booked()
    no_video.service = TrialCallService(
        no_video.uow,
        None,
        IntegrationService(
            no_video.uow, no_video.clock, AdminService(no_video.uow, no_video.clock)
        ),
        no_video.clock,
    )

    assert (await off.service.open(off.token)).state is TrialState.OFF
    assert (await no_video.service.open(no_video.token)).state is TrialState.OFF
    assert off.video.created == []


async def test_a_cancelled_booking_has_no_trial_call(setup: Setup) -> None:
    await BookingService(setup.uow, setup.clock, TEST_TOKEN_KEY).cancel(setup.token)

    view = await setup.service.open(setup.token)

    assert view.state is TrialState.INACTIVE
    assert setup.video.created == []


async def test_after_the_start_there_is_no_trial_call(setup: Setup) -> None:
    setup.clock.advance(SLOT_0900 - NOW)

    assert (await setup.service.open(setup.token)).state is TrialState.TOO_LATE


async def test_an_unknown_token_is_not_found(setup: Setup) -> None:
    for call in (setup.service.view, setup.service.open, setup.service.join_link):
        with pytest.raises(DomainError, match="booking_not_found"):
            await call("unknown")


async def test_a_trial_call_lost_in_a_race_deletes_its_own_meeting(setup: Setup) -> None:
    """Two requests open at once: the second finds the first stored and removes its meeting."""
    original = setup.video.create_meeting
    raced = False

    async def racing(request):  # type: ignore[no-untyped-def]
        nonlocal raced
        ref = await original(request)
        if not raced:
            raced = True
            await setup.service.open(setup.token)  # the other request stores its own meeting
        return ref

    setup.video.create_meeting = racing  # type: ignore[method-assign]

    view = await setup.service.open(setup.token)

    assert view.state is TrialState.ACTIVE
    assert setup.uow.state.bookings[setup.booking_id].trial_count == 1
    assert len(setup.video.deleted) == 1


async def test_cancelling_plans_the_removal_of_the_trial_meeting(setup: Setup) -> None:
    await setup.service.open(setup.token)

    cancelled = await BookingService(setup.uow, setup.clock, TEST_TOKEN_KEY).cancel(setup.token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert JobType.DELETE_TRIAL_CALL in setup.jobs()
