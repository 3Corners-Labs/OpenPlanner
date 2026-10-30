from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.booking import BookingStatus
from app.domain.closure import RETENTION_AFTER_CLOSURE
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.direct_queue import DirectAvailability
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType
from app.domain.scheduling import Offer
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import NOTICES, three_groups
from tests.support.domain import (
    ADA,
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    with_team_invite,
)

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP_ADMIN = Principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OTHER_TEAM = Principal(
    "g-2", "G2", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"andere-team"})
)
NEXT_MONDAY = SLOT_0900 + timedelta(days=7)
UNTIL_TUESDAY = ClosureInput(None, SLOT_0900 + timedelta(days=1), NOTICES)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(three_groups())


async def _book(bookings: BookingService, start: datetime = SLOT_0900, slug: str = "demo") -> int:
    receipt = await bookings.book(group_slug=slug, start=start, contact=ADA, locale="nl")
    return receipt.booking.id or 0


def _jobs(uow: InMemoryUnitOfWork, booking_id: int) -> list[JobType]:
    return [job.type for job in uow.state.jobs[booking_id]]


def _status(uow: InMemoryUnitOfWork, booking_id: int) -> BookingStatus:
    return uow.state.bookings[booking_id].status


# --- closing ------------------------------------------------------------------------------


async def test_closing_suspends_covered_bookings_and_keeps_their_data(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    inside = await _book(bookings)
    outside = await _book(bookings, NEXT_MONDAY)

    count = await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    assert count == 1
    booking = uow.state.bookings[inside]
    assert booking.status is BookingStatus.SUSPENDED
    assert booking.contact is not None  # nothing deleted (product owner, 2026-10-25)
    assert _jobs(uow, inside) == [JobType.END_WXCC_TASK, JobType.SEND_SUSPENSION, JobType.PURGE_PII]
    purge = uow.state.jobs[inside][-1]
    assert purge.run_at == UNTIL_TUESDAY.ends_at + RETENTION_AFTER_CLOSURE  # type: ignore[operator]
    assert _status(uow, outside) is BookingStatus.CONFIRMED
    actions = [(e.action, e.detail, e.group) for e in uow.state.audit]
    assert actions == [("closure.manage", "closure.start,bookings:1", "demo")]


async def test_open_ended_closure_has_no_purge_until_it_ends(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings, NEXT_MONDAY)

    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))
    assert _jobs(uow, booking_id) == [JobType.END_WXCC_TASK, JobType.SEND_SUSPENSION]

    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)
    assert _jobs(uow, booking_id) == [
        JobType.END_WXCC_TASK,
        JobType.SEND_SUSPENSION,
        JobType.PURGE_PII,
    ]
    assert uow.state.jobs[booking_id][-1].run_at == NOW + RETENTION_AFTER_CLOSURE


async def test_closed_group_offers_and_accepts_no_slots(
    closures: ClosureService, bookings: BookingService
) -> None:
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    assert await bookings.availability("demo", MONDAY, MONDAY) == []
    assert await bookings.availability(
        "demo", MONDAY + timedelta(days=7), MONDAY + timedelta(days=7)
    )
    with pytest.raises(DomainError, match="group_closed"):
        await _book(bookings)
    group = await bookings.get_group("demo")
    notice = await bookings.closure_notice(group)
    assert notice is not None
    assert notice.notice("en") == NOTICES["en"]


async def test_upcoming_closure_starts_later(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    early = await _book(bookings)
    starts = ClosureInput(SLOT_0930, SLOT_0930 + timedelta(hours=1), NOTICES)

    assert await closures.close_group(GROUP_ADMIN, "demo", starts) == 0
    assert _status(uow, early) is BookingStatus.CONFIRMED
    assert [s.start for s in await bookings.availability("demo", MONDAY, MONDAY)] == [SLOT_0900]


async def test_a_past_start_means_now(closures: ClosureService, uow: InMemoryUnitOfWork) -> None:
    await closures.close_group(
        GROUP_ADMIN, "demo", ClosureInput(NOW - timedelta(days=3), None, NOTICES)
    )

    assert next(iter(uow.state.closures.values())).starts_at == NOW


async def test_closing_twice_or_with_bad_input_is_refused(
    closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError, match="ends_at"):
        await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, NOW, NOTICES))
    with pytest.raises(DomainError, match="notice_en"):
        await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, {"nl": "Dicht"}))
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    with pytest.raises(DomainError) as error:
        await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    assert error.value.code is ErrorCode.CLOSURE_EXISTS
    assert len(uow.state.closures) == 1


async def test_other_groups_admin_cannot_close_or_touch_bookings(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    with pytest.raises(DomainError, match="not_permitted"):
        await closures.close_group(OTHER_TEAM, "demo", UNTIL_TUESDAY)
    with pytest.raises(DomainError, match="not_permitted"):
        await closures.cancel(OTHER_TEAM, "demo", booking_id)
    # Through their own group a booking of another group is "not found", never touched.
    with pytest.raises(DomainError, match="booking_not_found"):
        await closures.cancel(OTHER_TEAM, "andere-team", booking_id)
    assert _status(uow, booking_id) is BookingStatus.SUSPENDED
    denied = [e for e in uow.state.audit if e.outcome is Outcome.DENIED]
    assert len(denied) == 2


# --- the citizen --------------------------------------------------------------------------


async def test_citizen_can_reschedule_a_suspended_booking_after_the_deadline(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await bookings.book(group_slug="demo", start=SLOT_0900, contact=ADA, locale="nl")
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    clock.advance(SLOT_0900 - NOW - timedelta(minutes=1))  # past the change deadline

    details = await bookings.find(receipt.management_token)
    assert details.changeable
    assert details.closure is not None
    assert details.closure.notice("nl") == NOTICES["nl"]

    moved = await bookings.reschedule(receipt.management_token, NEXT_MONDAY)
    assert moved.booking.status is BookingStatus.CONFIRMED
    assert moved.booking.contact == uow.state.bookings[receipt.booking.id or 0].contact
    assert _status(uow, receipt.booking.id or 0) is BookingStatus.CANCELLED


async def test_citizen_can_cancel_a_suspended_booking(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await bookings.book(group_slug="demo", start=SLOT_0900, contact=ADA, locale="nl")
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    cancelled = await bookings.cancel(receipt.management_token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert JobType.SEND_CANCELLATION in _jobs(uow, cancelled.id or 0)


async def test_no_direct_call_while_closed(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.rules[1] = [
        replace(r, window=replace(r.window, offer=Offer.BOTH)) for r in uow.state.rules[1]
    ]
    clock.advance(SLOT_0900 - NOW)  # Monday 09:00, the group is open
    assert await bookings.direct_call_availability("demo") is DirectAvailability.AVAILABLE

    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))

    assert await bookings.direct_call_availability("demo") is DirectAvailability.UNAVAILABLE
    with pytest.raises(DomainError, match="direct_call_unavailable"):
        await bookings.start_direct(group_slug="demo", contact=ADA, locale="nl")


# --- updates and lifting ------------------------------------------------------------------


async def test_update_mails_everyone_on_hold_and_suspends_newly_covered(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await _book(bookings)
    later = await _book(bookings, NEXT_MONDAY)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    new_end = NEXT_MONDAY + timedelta(days=1)
    texts = {"nl": "Nog steeds dicht.", "en": "Still closed."}

    outcome = await closures.update(GROUP_ADMIN, "demo", new_end, texts, notify=True)

    assert (outcome.notified, outcome.suspended) == (1, 1)
    assert _jobs(uow, first) == [
        JobType.END_WXCC_TASK,
        JobType.SEND_SUSPENSION,
        JobType.PURGE_PII,
        JobType.SEND_CLOSURE_UPDATE,
    ]
    assert uow.state.jobs[first][2].run_at == new_end + RETENTION_AFTER_CLOSURE
    assert _status(uow, later) is BookingStatus.SUSPENDED
    assert _jobs(uow, later) == [JobType.END_WXCC_TASK, JobType.SEND_SUSPENSION, JobType.PURGE_PII]
    stored = next(iter(uow.state.closures.values()))
    assert stored.notices == texts


async def test_update_without_notify_sends_nothing_and_checks_the_end(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await _book(bookings)
    with pytest.raises(DomainError, match="closure_not_found"):
        await closures.update(GROUP_ADMIN, "demo", None, NOTICES, notify=True)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    outcome = await closures.update(GROUP_ADMIN, "demo", None, NOTICES, notify=False)
    assert outcome.notified == 0
    assert _jobs(uow, first) == [
        JobType.END_WXCC_TASK,
        JobType.SEND_SUSPENSION,
    ]  # open-ended again: no purge yet
    with pytest.raises(DomainError, match="ends_at"):
        await closures.update(GROUP_ADMIN, "demo", NOW, NOTICES, notify=False)


async def test_lifting_can_reinstate_what_still_goes_ahead(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings, NEXT_MONDAY)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))

    outcome = await closures.lift(GROUP_ADMIN, "demo", reinstate=True)

    assert (outcome.done, outcome.skipped) == (1, 0)
    assert _status(uow, booking_id) is BookingStatus.CONFIRMED
    assert _jobs(uow, booking_id)[1] == JobType.SEND_REINSTATEMENT
    assert await bookings.closure_notice(await bookings.get_group("demo")) is None


async def test_lifting_an_upcoming_closure_empties_it(
    closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(NEXT_MONDAY, None, NOTICES))

    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)

    stored = next(iter(uow.state.closures.values()))
    assert stored.starts_at == stored.ends_at == NOW
    with pytest.raises(DomainError, match="closure_not_found"):
        await closures.lift(GROUP_ADMIN, "demo", reinstate=False)


# --- handling bookings on hold -------------------------------------------------------------


async def test_reinstate_is_refused_while_still_closed_or_too_late(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    with pytest.raises(DomainError, match="group_closed"):
        await closures.reinstate(GROUP_ADMIN, "demo", booking_id)
    outcome = await closures.reinstate_all(GROUP_ADMIN, "demo")
    assert (outcome.done, outcome.skipped) == (0, 1)
    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)
    clock.advance(SLOT_0900 - NOW)
    with pytest.raises(DomainError, match="booking_locked"):
        await closures.reinstate(GROUP_ADMIN, "demo", booking_id)


async def test_reinstate_one_booking(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)

    await closures.reinstate(GROUP_ADMIN, "demo", booking_id)

    assert _status(uow, booking_id) is BookingStatus.CONFIRMED
    with pytest.raises(DomainError, match="booking_not_active"):
        await closures.reinstate(GROUP_ADMIN, "demo", booking_id)


async def test_admin_moves_a_suspended_booking(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    assert (await closures.booking(GROUP_ADMIN, "demo", booking_id)).id == booking_id

    moved = await closures.move(GROUP_ADMIN, "demo", booking_id, NEXT_MONDAY)

    assert moved.start == NEXT_MONDAY
    assert moved.status is BookingStatus.CONFIRMED
    assert moved.rescheduled_from_id == booking_id
    assert JobType.SEND_CHANGE in _jobs(uow, moved.id or 0)
    assert _status(uow, booking_id) is BookingStatus.CANCELLED
    assert _jobs(uow, booking_id) == [
        JobType.END_WXCC_TASK,
        JobType.DELETE_VIDEO_ROOM,
        JobType.PURGE_PII,
    ]
    with pytest.raises(DomainError, match="booking_not_active"):
        await closures.move(GROUP_ADMIN, "demo", moved.id or 0, SLOT_0930)


async def test_cancel_one_or_all(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first = await _book(bookings)
    second = await _book(bookings, SLOT_0930)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    await closures.cancel(GROUP_ADMIN, "demo", first)
    outcome = await closures.cancel_all(GROUP_ADMIN, "demo")

    assert outcome.done == 1
    assert {_status(uow, first), _status(uow, second)} == {BookingStatus.CANCELLED}
    assert JobType.SEND_CANCELLATION in _jobs(uow, second)


async def test_cancelling_a_booking_on_hold_withdraws_the_team_invite(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.groups[1] = with_team_invite(uow.state.groups[1])
    booking_id = await _book(bookings)
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)

    await closures.cancel(GROUP_ADMIN, "demo", booking_id)

    assert JobType.SEND_TEAM_CANCEL in _jobs(uow, booking_id)


async def test_overview_lists_bookings_on_hold(
    closures: ClosureService, bookings: BookingService
) -> None:
    await _book(bookings)
    await _book(bookings, NEXT_MONDAY)

    before = await closures.overview(GROUP_ADMIN, "demo")
    assert before.closure is None
    assert before.confirmed_ahead == 2
    await closures.close_group(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    after = await closures.overview(GROUP_ADMIN, "demo")
    assert after.closure is not None
    assert [b.start for b in after.suspended] == [SLOT_0900]
    assert after.confirmed_ahead == 1


# --- a whole organisation -------------------------------------------------------------------


async def test_org_admin_closes_and_lifts_every_group_of_the_organisation(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _book(bookings)
    await _book(bookings, slug="demo-twee")
    await _book(bookings, slug="andere-team")
    await closures.close_group(ORG_ADMIN, "demo-twee", UNTIL_TUESDAY)

    outcomes = await closures.close_organisation(ORG_ADMIN, "demo", UNTIL_TUESDAY)

    assert [(o.slug, o.changed, o.bookings) for o in outcomes] == [
        ("demo", True, 1),
        ("demo-twee", False, 0),  # already closed: left as it is
    ]
    states = await closures.organisation_overview(ORG_ADMIN, "demo")
    assert [(s.summary.group.slug, s.closure is not None, s.suspended) for s in states] == [
        ("demo", True, 1),
        ("demo-twee", True, 1),
    ]
    assert not uow.state.closures.keys() - {c.id for c in uow.state.closures.values()}
    assert all(c.group_id != 2 for c in uow.state.closures.values())  # other organisation

    lifted = await closures.lift_organisation(ORG_ADMIN, "demo", reinstate=False)
    assert [(o.slug, o.changed) for o in lifted] == [("demo", True), ("demo-twee", True)]
    again = await closures.lift_organisation(ORG_ADMIN, "demo", reinstate=False)
    assert [o.changed for o in again] == [False, False]
    audited = {(e.action, e.group) for e in uow.state.audit if e.outcome is Outcome.SUCCESS}
    assert ("organisation.closure", "demo") in audited


async def test_group_admin_cannot_close_the_organisation(closures: ClosureService) -> None:
    with pytest.raises(DomainError, match="not_permitted"):
        await closures.close_organisation(GROUP_ADMIN, "demo", UNTIL_TUESDAY)
    with pytest.raises(DomainError, match="not_permitted"):
        await closures.organisation_overview(ORG_ADMIN, "andere")


async def test_superuser_closes_any_organisation(
    closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    outcomes = await closures.close_organisation(SUPER, "andere", UNTIL_TUESDAY)

    assert [o.slug for o in outcomes] == ["andere-team"]
    assert datetime.now(UTC) > NOW  # sanity: tests use the fixed clock, not the wall clock
