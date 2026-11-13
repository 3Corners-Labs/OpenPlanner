"""A call event writes its usage facts in the transaction that records it (ADR 0117 step 3)."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import Booking, BookingKind, BookingStatus, MeetingRef
from app.domain.calls import CallState, EventKind, MeetingStatus, apply_event, new_status
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.meeting_status_service import MeetingStatusService
from app.domain.services import BookingService
from app.domain.usage import FactKind, UsageFact
from app.domain.webhook_replay import INSTALLATION_SCOPE
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeVideo
from tests.fakes.persistence import InMemoryTransaction, InMemoryUnitOfWork, State
from tests.support.admin import GROUP_ADMIN
from tests.support.domain import ADA, SLOT_0900, TEST_TOKEN_KEY, make_group, window
from tests.support.integration import GROUP, make_booking
from tests.support.webhooks import WXCC

DAY = date(2026, 1, 5)


def _facts(uow: InMemoryUnitOfWork) -> list[UsageFact]:
    return [f for f in uow.state.usage_facts if isinstance(f, UsageFact)]


def _kinds(uow: InMemoryUnitOfWork) -> list[FactKind]:
    return [f.kind for f in _facts(uow)]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(SLOT_0900 - timedelta(minutes=5))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.groups[1] = GROUP
    state.bookings[1] = make_booking(
        id=1, slot_id=1, meeting=MeetingRef("meeting-1", "https://site.webex.com/m")
    )
    return InMemoryUnitOfWork(state)


@pytest.fixture
def integration(uow: InMemoryUnitOfWork, clock: FixedClock) -> IntegrationService:
    return IntegrationService(uow, clock, AdminService(uow, clock))


async def test_a_work_item_and_its_acceptance_are_facts_with_the_wait(
    integration: IntegrationService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await integration.record(1, EventKind.TASK_CREATED)
    clock.advance(timedelta(seconds=40))
    await integration.record(1, EventKind.TASK_CONNECTED)

    created, connected = _facts(uow)
    assert (created.kind, connected.kind) == (FactKind.CALL_CREATED, FactKind.CALL_CONNECTED)
    assert connected.value_seconds == 40
    assert (created.organisation_id, created.group_id) == (1, 1)
    assert created.booking_kind is BookingKind.SCHEDULED


async def test_a_whole_call_writes_each_fact_once(
    integration: IntegrationService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    clock.advance(SLOT_0900 - clock.now())
    for kind in (
        EventKind.MEETING_STARTED,
        EventKind.GUEST_JOINED,
        EventKind.HOST_JOINED,
        EventKind.HOST_JOINED,  # a poll and a webhook can both report it
        EventKind.PARTICIPANT_LEFT,
        EventKind.MEETING_ENDED,
        EventKind.MEETING_ENDED,
    ):
        clock.advance(timedelta(minutes=1))
        await integration.record(1, kind)

    assert _kinds(uow) == [
        FactKind.CALL_HOST_JOINED,
        FactKind.CALL_GUEST_WAITED,
        FactKind.CALL_ENDED,
    ]
    host, waited, ended = _facts(uow)
    assert host.value_seconds == 180  # three minutes late
    assert waited.value_seconds == 60  # the guest came two minutes in, the host a minute later
    assert (ended.value_seconds, ended.participants) == (5 * 60, 2)


async def test_a_replayed_webhook_writes_no_second_fact(
    integration: IntegrationService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    for _ in range(3):
        outcome = await integration.record_meeting_event(
            "meeting-1",
            EventKind.HOST_JOINED,
            clock.now(),
            event_key="evt-1",
            scope=INSTALLATION_SCOPE,
            body_identity="",
        )

    assert outcome is EventOutcome.DUPLICATE
    assert _kinds(uow) == [FactKind.CALL_HOST_JOINED]


async def test_a_redelivered_work_item_event_writes_no_second_fact(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef

    uow.state.bookings[1] = replace(
        uow.state.bookings[1],
        contact_center_task=ContactCenterTaskRef("task-1", ContactCenterKind.WEBEX_CC),
    )
    service = IntegrationService(uow, clock, AdminService(uow, clock))
    await service.record(1, EventKind.TASK_CREATED)
    for _ in range(2):
        await service.record_task_event(
            "task-1", EventKind.TASK_CONNECTED, clock.now(), event_key="e-9", **WXCC
        )

    assert _kinds(uow) == [FactKind.CALL_CREATED, FactKind.CALL_CONNECTED]


async def test_a_failure_is_one_fact_with_a_code_and_never_the_providers_text(
    integration: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    await integration.record(
        1,
        EventKind.MEETING_FAILED,
        detail="rejected_403",
        message="Dear ada@example.org, host Ada Lovelace may not schedule",
    )

    (fact,) = _facts(uow)
    assert (fact.kind, fact.reason_code) == (FactKind.CALL_FAILED, "meeting.rejected")
    assert "ada" not in repr(fact).lower()


async def test_a_trial_step_counts_once_per_booking(
    integration: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    for kind in (
        EventKind.TRIAL_CREATED,
        EventKind.TRIAL_JOINED,
        EventKind.TRIAL_JOINED,
        EventKind.TRIAL_ENTERED,
        EventKind.TRIAL_ENTERED,
        EventKind.TRIAL_FAILED,
        EventKind.TRIAL_FAILED,
    ):
        await integration.record(1, kind)

    assert _kinds(uow) == [
        FactKind.TRIAL_CREATED,
        FactKind.TRIAL_JOINED,
        FactKind.TRIAL_ENTERED,
        FactKind.TRIAL_FAILED,
        FactKind.TRIAL_FAILED,
    ]


async def test_an_event_without_a_fact_loads_no_booking(
    integration: IntegrationService, uow: InMemoryUnitOfWork
) -> None:
    await integration.record(1, EventKind.JOIN_PAGE_OPENED)
    await integration.record(1, EventKind.MEETING_CREATED)

    assert _facts(uow) == []


async def test_the_fact_is_rolled_back_with_the_event(
    integration: IntegrationService, uow: InMemoryUnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: InMemoryTransaction, status: object) -> None:
        raise RuntimeError("the write failed")

    monkeypatch.setattr(InMemoryTransaction, "save_call_status", broken)

    with pytest.raises(RuntimeError):
        await integration.record(1, EventKind.TASK_CREATED)

    assert _facts(uow) == []
    assert uow.state.booking_events.get(1, []) == []


async def test_a_poll_writes_the_fact_of_each_transition_once(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.call_status[1] = apply_event(
        new_status(1), EventKind.MEETING_CREATED, SLOT_0900 - timedelta(days=1)
    )
    video = FakeVideo()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    service = MeetingStatusService(
        uow, clock, integration, lambda: video, lambda: timedelta(seconds=30)
    )
    clock.advance(SLOT_0900 - clock.now() + timedelta(minutes=1))
    video.statuses["meeting-1"] = MeetingStatus(
        CallState.IN_PROGRESS, participants=1, started_at=SLOT_0900
    )
    await service.day_overview(GROUP_ADMIN, "demo", DAY)  # the guest alone in the lobby
    clock.advance(timedelta(minutes=2))
    video.statuses["meeting-1"] = MeetingStatus(
        CallState.IN_PROGRESS, participants=2, host_joined=True, started_at=SLOT_0900
    )
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    clock.advance(timedelta(minutes=1))
    await service.day_overview(GROUP_ADMIN, "demo", DAY)  # the same state again: nothing new

    assert _kinds(uow) == [FactKind.CALL_HOST_JOINED, FactKind.CALL_GUEST_WAITED]
    host, waited = _facts(uow)
    assert host.value_seconds == 3 * 60
    assert waited.value_seconds == 2 * 60  # seen alone at +1, the host at +3
    assert uow.state.call_status[1].peak_participants == 2


async def test_a_poll_that_sees_both_at_once_cannot_tell_who_came_first(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.call_status[1] = apply_event(
        new_status(1), EventKind.MEETING_CREATED, SLOT_0900 - timedelta(days=1)
    )
    video = FakeVideo()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    service = MeetingStatusService(
        uow, clock, integration, lambda: video, lambda: timedelta(seconds=30)
    )
    clock.advance(SLOT_0900 - clock.now() + timedelta(minutes=2))
    video.statuses["meeting-1"] = MeetingStatus(
        CallState.IN_PROGRESS, participants=2, host_joined=True, started_at=SLOT_0900
    )

    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    assert _kinds(uow) == [FactKind.CALL_HOST_JOINED]  # no wait is claimed that is not known


async def test_the_end_of_a_polled_meeting_carries_the_peak_not_the_empty_room(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.call_status[1] = apply_event(
        new_status(1), EventKind.MEETING_CREATED, SLOT_0900 - timedelta(days=1)
    )
    video = FakeVideo()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    service = MeetingStatusService(
        uow, clock, integration, lambda: video, lambda: timedelta(seconds=30)
    )
    clock.advance(SLOT_0900 - clock.now() + timedelta(minutes=2))
    video.statuses["meeting-1"] = MeetingStatus(
        CallState.IN_PROGRESS, participants=3, host_joined=True, started_at=SLOT_0900
    )
    await service.day_overview(GROUP_ADMIN, "demo", DAY)
    clock.advance(timedelta(minutes=2))
    video.statuses["meeting-1"] = MeetingStatus(
        CallState.ENDED, ended_at=SLOT_0900 + timedelta(minutes=12)
    )

    await service.day_overview(GROUP_ADMIN, "demo", DAY)

    ended = [f for f in _facts(uow) if f.kind is FactKind.CALL_ENDED]
    assert [(f.participants, f.value_seconds) for f in ended] == [(3, 12 * 60)]


# --- a participant leaving a direct call ---------------------------------------------------


def _direct_site() -> tuple[BookingService, InMemoryUnitOfWork]:
    state = State()
    state.groups[1] = make_group(id=1, slug="balie", direct_call_max_concurrent=3)
    from app.domain.scheduling import Offer, ScheduleRule

    state.rules[1] = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 2, Offer.BOTH))]
    uow = InMemoryUnitOfWork(state)
    from datetime import UTC, datetime

    clock = FixedClock(datetime(2026, 1, 5, 8, 15, tzinfo=UTC))
    return BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0), uow


async def test_leaving_a_direct_call_writes_the_wait_and_it_is_not_repeated() -> None:
    service, uow = _direct_site()
    receipt = await service.start_direct(group_slug="balie", contact=ADA, locale="nl")

    await service.leave_direct(receipt.management_token)
    await service.leave_direct(receipt.management_token)

    left = [f for f in _facts(uow) if f.kind is FactKind.CALL_WAITING_LEFT]
    assert len(left) == 1
    assert (left[0].reason_code, left[0].booking_kind) == ("waiting", BookingKind.DIRECT)
    assert left[0].value_seconds is not None
    assert left[0].value_seconds >= 0


async def test_a_direct_call_created_carries_the_calls_open_at_that_moment() -> None:
    service, uow = _direct_site()
    first = await service.start_direct(group_slug="balie", contact=ADA, locale="nl")
    second = await service.start_direct(group_slug="balie", contact=ADA, locale="nl")
    integration = IntegrationService(
        uow, FixedClock(SLOT_0900), AdminService(uow, FixedClock(SLOT_0900))
    )

    await integration.record(first.booking.id or 0, EventKind.TASK_CREATED)
    await integration.record(second.booking.id or 0, EventKind.TASK_CREATED)

    loads = [f.participants for f in _facts(uow) if f.kind is FactKind.CALL_CREATED]
    assert loads == [2, 2]  # both are waiting when each is created
    assert all(
        isinstance(b, Booking) and b.status is BookingStatus.CONFIRMED
        for b in uow.state.bookings.values()
    )
