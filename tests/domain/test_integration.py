from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, Outcome, Source
from app.domain.authorization import Role
from app.domain.booking import Booking, BookingStatus, Contact, MeetingRef, TrialCall
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
from app.domain.integration import WEBHOOK_MEMORY, EventOutcome, IntegrationService
from app.domain.job_plan import JobStatus, JobType, PlannedJob
from app.domain.request_log import REQUEST_EVENT
from app.domain.scheduling import PhoneMode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, SLOT_0900, make_group
from tests.support.principals import principal

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
    first_name="Ada",
    last_name="Lovelace",
    email="ada@example.org",
    phone=None,
    phone_mode=PhoneMode.OPTIONAL,
)
GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OUTSIDER = principal("x-1", "X", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset())


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


def test_a_failed_meeting_shows_as_failed() -> None:
    status = apply_event(new_status(123), EventKind.MEETING_FAILED, NOW)

    assert status.call is CallState.FAILED


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
    recorded, unknown = EventOutcome.RECORDED, EventOutcome.UNKNOWN
    started = EventKind.MEETING_STARTED
    assert await service.record_meeting_event("meeting-1_I_42", started, NOW) is recorded
    assert await service.record_task_event("task-1", EventKind.TASK_CONNECTED, NOW) is recorded
    assert await service.record_meeting_event("unknown", started, NOW) is unknown
    assert await service.record_task_event("unknown", EventKind.TASK_ENDED, NOW) is unknown

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


@pytest.mark.parametrize(
    ("day", "first", "last"),
    [
        # 25 October 2026 has 25 hours: 00:00 CEST is 22:00 UTC, 23:30 CET is 22:30 UTC.
        (date(2026, 10, 25), datetime(2026, 10, 24, 22, 0), datetime(2026, 10, 25, 22, 30)),
        # 29 March 2026 has 23 hours: 00:00 CET is 23:00 UTC, 23:30 CEST is 21:30 UTC.
        (date(2026, 3, 29), datetime(2026, 3, 28, 23, 0), datetime(2026, 3, 29, 21, 30)),
    ],
)
async def test_day_overview_covers_the_whole_local_day_across_dst(
    service: IntegrationService,
    uow: InMemoryUnitOfWork,
    day: date,
    first: datetime,
    last: datetime,
) -> None:
    before, after = first - timedelta(minutes=30), last + timedelta(minutes=30)
    for booking_id, start in enumerate((before, first, last, after), start=200):
        uow.state.bookings[booking_id] = _booking(id=booking_id, start=start.replace(tzinfo=UTC))

    overview = await service.day_overview(GROUP_ADMIN, "demo", day)

    assert [row.booking.id for row in overview] == [201, 202]


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


async def test_status_of_a_booking_for_the_waiting_page(service: IntegrationService) -> None:
    from app.domain.calls import CallState, TaskState

    assert (await service.status(123)).task is TaskState.NONE
    await service.record(123, EventKind.TASK_CREATED)
    status = await service.status(123)
    assert status.task is TaskState.QUEUED
    assert status.call is CallState.NONE


async def test_day_overview_lists_the_groups_bookings_with_status(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    await service.record(123, EventKind.TASK_CREATED)

    overview = await service.day_overview(GROUP_ADMIN, "demo", SLOT_0900.date())

    assert [(row.booking.id, row.status.task) for row in overview] == [(123, TaskState.QUEUED)]
    assert (
        await service.day_overview(GROUP_ADMIN, "demo", SLOT_0900.date() + timedelta(days=1)) == []
    )
    with pytest.raises(DomainError, match="not_permitted"):
        await service.day_overview(OUTSIDER, "demo", SLOT_0900.date())


async def test_a_replayed_webex_event_changes_the_counts_once(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    joined = EventKind.GUEST_JOINED

    first = await service.record_meeting_event("meeting-1", joined, NOW, event_key="k1")
    replay = await service.record_meeting_event("meeting-1", joined, NOW, event_key="k1")
    other = await service.record_meeting_event("meeting-1", joined, NOW, event_key="k2")

    assert (first, replay, other) == (
        EventOutcome.RECORDED,
        EventOutcome.DUPLICATE,
        EventOutcome.RECORDED,
    )
    assert uow.state.call_status[123].participants == 2
    assert len(uow.state.booking_events[123]) == 2


async def test_events_of_unknown_meetings_are_not_remembered(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    outcome = await service.record_meeting_event(
        "someone-elses-meeting", EventKind.GUEST_JOINED, NOW, event_key="k1"
    )

    assert outcome is EventOutcome.UNKNOWN
    assert uow.state.webhook_events == {}


async def test_remembered_events_are_forgotten_after_the_retention(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    later = NOW + WEBHOOK_MEMORY + timedelta(minutes=1)
    await service.record_meeting_event("meeting-1", EventKind.GUEST_JOINED, NOW, event_key="old")

    await service.record_meeting_event("meeting-1", EventKind.GUEST_JOINED, later, event_key="new")

    assert set(uow.state.webhook_events) == {("webex", "new")}


async def test_day_overview_covers_the_25_hour_day_when_clocks_go_back(
    service: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    # 25 October 2026 lasts 25 hours in Europe/Amsterdam; 23:30 local is 22:30 UTC.
    late = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    uow.state.bookings[123] = _booking(start=late, end=late + timedelta(minutes=30))

    on_the_day = await service.day_overview(GROUP_ADMIN, "demo", date(2026, 10, 25))
    next_day = await service.day_overview(GROUP_ADMIN, "demo", date(2026, 10, 26))

    assert [row.booking.id for row in on_the_day] == [123]
    assert next_day == []


async def test_the_timeline_carries_versions_jobs_and_the_bookings_requests(
    uow: InMemoryUnitOfWork,
) -> None:
    # ADR 0068: what ran, what failed, what Webex answered, and which version wrote it.
    clock = FixedClock(NOW)
    service = IntegrationService(uow, clock, AdminService(uow, clock), "0.4.0-rc.7")
    uow.state.jobs[123] = [PlannedJob(JobType.SEND_REMINDER, NOW, SLOT_0900)]
    mine = AuditEvent(
        NOW,
        "system",
        REQUEST_EVENT,
        Outcome.FAILURE,
        "rejected_400",
        "demo",
        "demo",
        "method:POST",
        Source.SYSTEM,
        123,
    )
    other = replace(mine, booking_id=124)
    job_event = replace(mine, action="job.create_video_room")
    uow.state.audit += [mine, other, job_event]

    await service.record(123, EventKind.MEETING_FAILED, detail="rejected_400")
    timeline = await service.timeline(GROUP_ADMIN, 123)

    assert timeline.events[-1].version == "0.4.0-rc.7"
    assert [(j.type, j.status) for j in timeline.jobs] == [
        (JobType.SEND_REMINDER, JobStatus.PENDING)
    ]
    assert timeline.requests == [mine]


# --- the trial call's own meeting (ADR 0071): its Webex events reach the timeline --------


TRIAL_ROOM = "trial-1"


@pytest.fixture
def trial_uow(uow: InMemoryUnitOfWork) -> InMemoryUnitOfWork:
    """The booking with an open trial call next to its own meeting."""
    booking = uow.state.bookings[123]
    trial = TrialCall(
        MeetingRef(TRIAL_ROOM, "https://site.webex.com/t"), NOW + timedelta(minutes=30)
    )
    uow.state.bookings[123] = replace(booking, trial=trial)
    return uow


def _kinds(uow: InMemoryUnitOfWork) -> list[EventKind]:
    return [e.kind for e in uow.state.booking_events.get(123, [])]


async def test_a_guest_entering_the_trial_call_is_on_the_timeline_not_in_the_call_status(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    outcome = await service.record_meeting_event(TRIAL_ROOM, EventKind.GUEST_JOINED, NOW)

    assert outcome is EventOutcome.RECORDED
    assert _kinds(trial_uow) == [EventKind.TRIAL_ENTERED]
    status = trial_uow.state.call_status[123]
    assert (status.participants, status.host_joined, status.call) == (0, False, CallState.NONE)


async def test_someone_leaving_the_trial_call_is_on_the_timeline(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    await service.record_meeting_event(TRIAL_ROOM, EventKind.GUEST_JOINED, NOW)
    await service.record_meeting_event(TRIAL_ROOM, EventKind.PARTICIPANT_LEFT, NOW)

    assert _kinds(trial_uow) == [EventKind.TRIAL_ENTERED, EventKind.TRIAL_LEFT]
    assert trial_uow.state.call_status[123].participants == 0


async def test_a_host_in_the_trial_call_is_noted_because_nobody_from_the_team_should_be_there(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    await service.record_meeting_event(TRIAL_ROOM, EventKind.HOST_JOINED, NOW)

    (event,) = trial_uow.state.booking_events[123]
    assert (event.kind, event.detail) == (EventKind.TRIAL_ENTERED, "host")
    assert trial_uow.state.call_status[123].host_joined is False  # the appointment is not started


async def test_the_trial_meetings_start_and_end_are_ignored(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    started = await service.record_meeting_event(TRIAL_ROOM, EventKind.MEETING_STARTED, NOW)
    ended = await service.record_meeting_event(TRIAL_ROOM, EventKind.MEETING_ENDED, NOW)

    assert (started, ended) == (EventOutcome.IGNORED, EventOutcome.IGNORED)
    assert _kinds(trial_uow) == []
    assert 123 not in trial_uow.state.call_status  # the appointment's call is untouched


async def test_an_instance_id_and_a_replay_of_a_trial_event_count_once(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    joined = EventKind.GUEST_JOINED

    first = await service.record_meeting_event(f"{TRIAL_ROOM}_I_9", joined, NOW, event_key="t1")
    replay = await service.record_meeting_event(TRIAL_ROOM, joined, NOW, event_key="t1")

    assert (first, replay) == (EventOutcome.RECORDED, EventOutcome.DUPLICATE)
    assert _kinds(trial_uow) == [EventKind.TRIAL_ENTERED]


async def test_a_closed_trial_call_no_longer_matches(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    booking = trial_uow.state.bookings[123]
    trial_uow.state.bookings[123] = replace(booking, trial=None)

    outcome = await service.record_meeting_event(TRIAL_ROOM, EventKind.GUEST_JOINED, NOW)

    assert outcome is EventOutcome.UNKNOWN
    assert _kinds(trial_uow) == []


async def test_the_appointments_own_meeting_still_counts_the_participant(
    service: IntegrationService, trial_uow: InMemoryUnitOfWork
) -> None:
    await service.record_meeting_event("meeting-1", EventKind.GUEST_JOINED, NOW)

    assert _kinds(trial_uow) == [EventKind.GUEST_JOINED]
    assert trial_uow.state.call_status[123].participants == 1
