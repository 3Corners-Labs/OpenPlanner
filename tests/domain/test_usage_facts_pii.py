"""No fact of any kind holds personal data, a booking id, a token or a link (ADR 0117 R8).

The facts are produced by the real producers - booking changes, call events, the poll, a
participant leaving a direct call, the own queue, the planner, the job runner's messages and
the closing of an ended slot - for participants with distinctive names, addresses and links.
Then every kind of ``FactKind`` must have been produced, and nothing of the distinctive data may
appear in any fact or in any row the rollup makes of it.
"""

from dataclasses import asdict, fields, replace
from datetime import UTC, datetime, timedelta

from app.domain import booking_changes
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import Booking, BookingKind, ChangeReason, MeetingRef
from app.domain.call_facts import FAILURE_CODES, LEAVE_REASONS
from app.domain.calls import EventKind
from app.domain.channels import ChannelKind
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType
from app.domain.message_usage import PURPOSES, record_message
from app.domain.planner_service import PlannerService
from app.domain.privacy import USAGE_FIELDS
from app.domain.queue_service import QueueService
from app.domain.scheduling import DispatchKind, Offer, ScheduleRule
from app.domain.services import BookingService, ContactInput
from app.domain.slot_close_service import SlotCloser
from app.domain.usage import MOVE_REASON, FactKind, StaffFact, UsageFact
from app.domain.usage_recording import record_closure_change
from app.domain.usage_rollup import roll
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.admin import ALERT_THRESHOLDS, GROUP_ADMIN
from tests.support.closures import NOTICES, three_groups
from tests.support.domain import (
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    TUESDAY_0900,
    book,
    book_pending,
    make_group,
    window,
)
from tests.support.principals import principal

LINK = "https://pii-meeting.example/j/9f8e7d6c"
SECRETS = (
    "zorbaxia",
    "quillfeather",
    "sentinel-pii",
    "5551",
    "pii-meeting",
    "9f8e7d6c",
    "OP-0001",
)
AGENT = principal("a-1", "A", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"}))
KEY = b"p" * 32


def _person(number: int) -> ContactInput:
    return ContactInput(
        first_name="Zorbaxia",
        last_name="Quillfeather",
        email=f"zorb.quill{number}@sentinel-pii.example",
        phone="+31 6 5551 0199",
    )


def _facts(uow: InMemoryUnitOfWork) -> list[UsageFact]:
    return [f for f in uow.state.usage_facts if isinstance(f, UsageFact)]


async def _booking_scenario() -> list[UsageFact]:
    """Booking changes, calls, the poll's events, rebooking, messages and the slot closing."""
    uow = InMemoryUnitOfWork(three_groups())
    clock = FixedClock(NOW)
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    closures = ClosureService(uow, clock, AdminService(uow, clock), bookings)
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    admin = AdminService(uow, clock, alert_thresholds=ALERT_THRESHOLDS)
    planner = PlannerService(uow, clock, admin, bookings)
    closer = SlotCloser(uow, clock, lambda: timedelta(minutes=15))
    await closer.run(clock.now())

    kept = await book(bookings, SLOT_0900, "demo", _person(1))
    moved = await bookings.reschedule(kept.management_token, SLOT_0930)
    await bookings.cancel(moved.management_token)
    pending = await book_pending(bookings, SLOT_0900, "demo", _person(2))
    async with uow.locked() as tx:
        stored = await tx.get_booking(pending.booking.id or 0)
        assert stored is not None
        await booking_changes.lapse_unconfirmed(tx, stored, clock.now() + timedelta(days=3))

    waiting = await book(bookings, SLOT_0900, "demo", _person(3))
    await planner.request_rebook(
        GROUP_ADMIN, "demo", waiting.booking.id or 0, ChangeReason.STAFFING
    )
    accepted = await bookings.reschedule(waiting.management_token, SLOT_0930 + timedelta(days=7))
    lapsing = await book(bookings, SLOT_0900, "demo", _person(4))
    await planner.request_rebook(
        GROUP_ADMIN, "demo", lapsing.booking.id or 0, ChangeReason.UNFORESEEN
    )
    async with uow.locked() as tx:
        stored = await tx.get_booking(lapsing.booking.id or 0)
        assert stored is not None
        await booking_changes.lapse(tx, stored, SLOT_0900 + timedelta(days=14))

    # Calls: a booking with a meeting and a work item, the hosts and guests, a failure, a trial.
    next_week = SLOT_0900 + timedelta(days=7)
    answered = await book(bookings, next_week, "demo", _person(6))
    silent = await book(bookings, next_week, "demo", _person(7))
    for receipt in (answered, silent):
        booking_id = receipt.booking.id or 0
        uow.state.bookings[booking_id] = replace(
            uow.state.bookings[booking_id], meeting=MeetingRef("m-" + str(booking_id), LINK)
        )
    answered_id, silent_id = answered.booking.id or 0, silent.booking.id or 0
    at = next_week
    for booking_id, kind, detail, offset in (
        (answered_id, EventKind.MEETING_CREATED, "", -60),
        (answered_id, EventKind.TASK_CREATED, "", -5),
        (answered_id, EventKind.TASK_CONNECTED, "", 0),
        (answered_id, EventKind.MEETING_STARTED, "", 1),
        (answered_id, EventKind.GUEST_JOINED, "", 2),
        (answered_id, EventKind.HOST_JOINED, "", 3),
        (answered_id, EventKind.MEETING_ENDED, "", 25),
        (answered_id, EventKind.TASK_FAILED, "zorbaxia@sentinel-pii.example 5551", 26),
        (answered_id, EventKind.MEETING_FAILED, "rejected_403", 27),
        (answered_id, EventKind.INTEGRATION_ERROR, f"polled:{LINK}", 28),
        (answered_id, EventKind.TRIAL_CREATED, "", -30),
        (answered_id, EventKind.TRIAL_JOINED, "browser", -29),
        (answered_id, EventKind.TRIAL_ENTERED, "", -28),
        (answered_id, EventKind.TRIAL_FAILED, "rejected_400", -27),
        (silent_id, EventKind.MEETING_CREATED, "", -60),
        (silent_id, EventKind.FALLBACK_QUEUED, "dispatch_failed", -5),
    ):
        await integration.record(booking_id, kind, at + timedelta(minutes=offset), detail)

    await record_message(
        uow, JobType.SEND_REMINDER, ChannelKind.SMS, answered_id, clock.now(), sent=True
    )
    await record_message(
        uow, JobType.SEND_CANCELLATION, ChannelKind.EMAIL, answered_id, clock.now(), sent=False
    )

    suspended = await book(bookings, SLOT_0900, "demo", _person(5))
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, TUESDAY_0900, NOTICES))
    async with uow.locked() as tx:
        stored = await tx.get_booking(suspended.booking.id or 0)
        group = await tx.get_group_by_id(1)
        assert stored is not None
        assert group is not None
        await record_closure_change(tx, FactKind.BOOKING_RESUMED, stored, group, clock.now())

    uow.state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]  # a shortfall
    clock.advance(timedelta(days=30))
    await closer.run(clock.now())
    assert accepted.booking.id is not None
    return _facts(uow)


async def _queue_scenario() -> list[UsageFact]:
    """A claim and a release in an own queue."""
    state = State(organisation_slugs={1: "demo"})
    state.groups[1] = make_group(
        id=1, organisation_id=1, slug="demo", dispatch_kind=DispatchKind.OPENPLANNER_QUEUE
    )
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.next_id = 100
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY), SLOT_0900, "demo", _person(8))
    booking_id = receipt.booking.id or 0
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id], meeting=MeetingRef("m", LINK)
    )
    clock.advance(SLOT_0900 - clock.now())
    queue = QueueService(uow, clock, AdminService(uow, clock), KEY)
    await queue.claim_next(AGENT, "demo")
    await queue.release(AGENT, "demo", booking_id)
    return _facts(uow)


async def _direct_scenario() -> list[UsageFact]:
    """A participant who gives up waiting for a direct call, with the call's creation."""
    state = State()
    state.groups[1] = make_group(id=1, slug="balie", direct_call_max_concurrent=3)
    state.rules[1] = [ScheduleRule(weekday=0, window=window("09:00", "10:00", 2, Offer.BOTH))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(datetime(2026, 1, 5, 8, 15, tzinfo=UTC))
    service = BookingService(uow, clock, TEST_TOKEN_KEY)
    receipt = await service.start_direct(group_slug="balie", contact=_person(9), locale="nl")
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    await integration.record(receipt.booking.id or 0, EventKind.TASK_CREATED)
    await service.leave_direct(receipt.management_token)
    return _facts(uow)


REASONS = (
    {MOVE_REASON, "fallback", "open", "accepted", "resolved"}
    | {r.value for r in ChangeReason}
    | set(LEAVE_REASONS)
    | set(PURPOSES.values())
    | {f"{part}.{code}" for part in ("task", "meeting", "integration") for code in FAILURE_CODES}
)
"""Every ``reason_code`` a fact may hold: each a member of a closed list of the application."""


async def test_every_kind_of_fact_is_produced_and_none_holds_personal_data() -> None:
    facts = [
        *await _booking_scenario(),
        *await _queue_scenario(),
        *await _direct_scenario(),
    ]

    assert {f.kind for f in facts} == set(FactKind)  # no kind without a producer under test
    for fact in facts:
        text = repr(fact).lower() + repr(asdict(fact)).lower()
        for secret in SECRETS:
            assert secret.lower() not in text, (fact.kind, secret)
        assert "http" not in text
        assert "@" not in text
        assert {f.name for f in fields(fact)} <= set(USAGE_FIELDS)
        assert fact.reason_code is None or fact.reason_code in REASONS, fact
        assert fact.locale in (None, "nl")
        assert fact.bucket_hour.minute == fact.bucket_hour.second == 0


async def test_the_rows_the_rollup_makes_hold_nothing_but_codes_and_numbers() -> None:
    facts = [*await _booking_scenario(), *await _direct_scenario(), *await _queue_scenario()]

    cells = roll([f for f in facts if not isinstance(f, StaffFact)])

    assert cells
    for cell in cells:
        text = repr(asdict(cell)).lower()
        for secret in SECRETS:
            assert secret.lower() not in text, (cell.metric, secret)
    metrics = {c.metric for c in cells}
    assert {"U2", "U3", "U8", "U11", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9"} <= metrics
    assert {"C1", "C2", "C3", "C5", "M1", "T1"} <= metrics


def test_the_fixtures_use_data_that_would_be_found_if_it_leaked() -> None:
    assert _person(1).first_name.lower() in SECRETS
    assert BookingKind.DIRECT is not Booking
