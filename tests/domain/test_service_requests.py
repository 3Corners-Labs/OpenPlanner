"""Requests, work items, the watchdog and the deletion moments (ADR 0121 step 5, domain).

The pure rules of ``app.domain.service_requests``: the lifecycle of a request and its work item,
the lease, the watchdog round (A33, A34), the deletion moment of the request-level record and the
resolver that reads personal data from one place (decision D1, A20).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingStatus, Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.pool_accounting import Commitment
from app.domain.scheduling import PhoneMode
from app.domain.service_flows import IntakeMode, InteractionKind
from app.domain.service_requests import (
    ACTIVE_ITEM,
    TERMINAL,
    BookingPhase,
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    WorkItem,
    WorkItemState,
    appointment_request,
    booking_phase,
    check_startable,
    claim,
    complete,
    contact_of,
    data_deletion_at,
    is_request_work,
    release,
    start,
    watchdog_deadline,
    watchdog_outcome,
)
from tests.support.service_flows import legacy, revision

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
END = T0 + timedelta(minutes=30)
FLOW = FlowRef(7, 1)
AGENT = "agent-pseudonym"


def appointment(**changes: object) -> ServiceRequest:
    """Return a stored, queued request of booking 11 that ends at ``END``."""
    made = appointment_request(
        organisation_id=1,
        group_id=2,
        flow=FLOW,
        booking_id=11,
        booking_end=END,
        now=T0,
        unresolved_hours=24,
    )
    return replace(made, id=5, **changes)  # type: ignore[arg-type]


def on_demand(**changes: object) -> ServiceRequest:
    """Return a stored request without an appointment, accepted at ``T0``."""
    base = ServiceRequest(
        id=6,
        organisation_id=1,
        group_id=2,
        flow=FLOW,
        intake=IntakeMode.ON_DEMAND,
        booking_id=None,
        handling=HandlingState.QUEUED,
        created_at=T0,
        watchdog_at=watchdog_deadline(T0, 24),
        personal_data=PersonalDataState.PRESENT,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def claimed(request: ServiceRequest | None = None) -> tuple[ServiceRequest, WorkItem]:
    """Return ``request`` (an appointment by default) claimed at ``T0`` with a 5-minute lease."""
    assigned, item = claim(request or appointment(), None, now=T0, agent=AGENT, lease_minutes=5)
    return assigned, replace(item, id=9)


def started(at: datetime = T0) -> tuple[ServiceRequest, WorkItem]:
    """Return an appointment request started at ``at`` on pool 3 for 30 minutes plus 5."""
    request, item = claimed()
    return start(
        request, item, now=at, pool_id=3, planned_until=at + timedelta(minutes=35), conflict=False
    )


def test_an_appointment_request_watches_from_the_reservation_end() -> None:
    request = appointment()

    assert request.handling is HandlingState.QUEUED
    assert request.watchdog_at == END + timedelta(hours=24)
    assert request.key == ("booking", 11)
    assert request.personal_data is PersonalDataState.NOT_COLLECTED
    assert not request.terminal


def test_a_request_without_a_booking_has_a_key_of_its_own() -> None:
    assert on_demand().key == ("request", 6)


def test_an_appointment_needs_its_booking_and_on_demand_has_none() -> None:
    with pytest.raises(DomainError) as missing:
        replace(appointment(), booking_id=None)
    with pytest.raises(DomainError) as extra:
        replace(on_demand(), booking_id=3)

    assert missing.value.field == extra.value.field == "booking_id"


def test_a_terminal_request_carries_its_resolved_time_and_only_then() -> None:
    with pytest.raises(DomainError) as early:
        replace(appointment(), resolved_at=T0)
    with pytest.raises(DomainError) as late:
        replace(appointment(), handling=HandlingState.COMPLETED)

    assert early.value.field == late.value.field == "resolved_at"
    assert TERMINAL == {HandlingState.COMPLETED, HandlingState.CANCELLED, HandlingState.EXPIRED}


def test_the_watchdog_makes_an_expired_request_for_a_booking_nobody_claimed() -> None:
    made = appointment_request(
        organisation_id=1,
        group_id=2,
        flow=FLOW,
        booking_id=11,
        booking_end=END,
        now=END + timedelta(days=1),
        unresolved_hours=24,
        handling=HandlingState.EXPIRED,
    )

    assert made.terminal
    assert made.resolved_at == END + timedelta(days=1)


def test_a_claim_assigns_the_request_with_a_lease() -> None:
    request, item = claimed()

    assert request.handling is HandlingState.ASSIGNED
    assert item.state is WorkItemState.ASSIGNED
    assert item.lease_until == T0 + timedelta(minutes=5)
    assert item.request_id == 5
    assert item.active
    assert not item.started
    assert item.commitment(request.key) is None  # an appointment's hold is its booking


def test_a_claim_is_refused_with_an_active_work_item_or_when_finished() -> None:
    request, item = claimed()
    with pytest.raises(DomainError) as taken:
        claim(request, item, now=T0, agent=AGENT, lease_minutes=5)
    done = replace(appointment(), handling=HandlingState.EXPIRED, resolved_at=T0)
    with pytest.raises(DomainError) as finished:
        claim(done, None, now=T0, agent=AGENT, lease_minutes=5)
    recovering = replace(appointment(), handling=HandlingState.RECOVERY_REQUIRED)
    with pytest.raises(DomainError) as recovery:
        claim(recovering, None, now=T0, agent=AGENT, lease_minutes=5)

    assert taken.value.code is ErrorCode.ALREADY_CLAIMED
    assert finished.value.code is recovery.value.code is ErrorCode.BOOKING_NOT_ACTIVE


def test_a_start_within_the_lease_occupies_a_unit_of_the_pool() -> None:
    request, item = started(T0 + timedelta(minutes=4))

    assert request.handling is HandlingState.IN_PROGRESS
    assert item.state is WorkItemState.IN_PROGRESS
    assert item.lease_until is None
    assert item.commitment(request.key) == Commitment(
        ("booking", 11),
        T0 + timedelta(minutes=4),
        T0 + timedelta(minutes=39),
        in_progress=True,
    )


def test_a_start_after_the_lease_is_refused() -> None:
    request, item = claimed()

    with pytest.raises(DomainError) as late:
        start(
            request,
            item,
            now=T0 + timedelta(minutes=5),
            pool_id=3,
            planned_until=T0 + timedelta(hours=1),
            conflict=False,
        )

    assert late.value.code is ErrorCode.LEASE_EXPIRED


def test_only_an_assigned_work_item_starts() -> None:
    _, running = started()
    with pytest.raises(DomainError) as twice:
        check_startable(running, T0)
    with pytest.raises(DomainError) as nothing:
        check_startable(None, T0)

    assert twice.value.code is ErrorCode.REQUEST_IN_PROGRESS
    assert nothing.value.code is ErrorCode.NOT_CLAIMED


def test_a_start_records_a_conflict_during_an_overrun() -> None:
    request, item = claimed()

    _, conflicted = start(
        request, item, now=T0, pool_id=3, planned_until=T0 + timedelta(minutes=35), conflict=True
    )

    assert conflicted.start_conflict


def test_complete_ends_the_work_and_releases_the_unit_once() -> None:
    request, item = started()

    done, ended = complete(request, item, now=T0 + timedelta(minutes=20))

    assert done.handling is HandlingState.COMPLETED
    assert done.resolved_at == T0 + timedelta(minutes=20)
    assert ended.state is WorkItemState.COMPLETED
    assert ended.ended_at == T0 + timedelta(minutes=20)
    assert ended.commitment(done.key) is None
    with pytest.raises(DomainError) as again:
        complete(done, ended, now=T0 + timedelta(minutes=21))
    assert again.value.code is ErrorCode.NOT_CLAIMED


def test_unstarted_work_cannot_be_completed_and_started_work_cannot_be_released() -> None:
    request, item = claimed()
    with pytest.raises(DomainError) as early:
        complete(request, item, now=T0)
    running_request, running = started()
    with pytest.raises(DomainError) as running_away:
        release(running_request, running, now=T0)

    assert early.value.code is ErrorCode.REQUEST_NOT_STARTED
    assert running_away.value.code is ErrorCode.REQUEST_IN_PROGRESS


def test_a_release_puts_the_request_back_in_the_queue() -> None:
    request, item = claimed()

    back, ended = release(request, item, now=T0 + timedelta(minutes=1))

    assert back.handling is HandlingState.QUEUED
    assert back.resolved_at is None
    assert ended.state is WorkItemState.RELEASED
    assert not ended.active
    assert ended.lease_until is None


def test_work_in_recovery_is_completed_and_keeps_its_unit_until_then() -> None:
    request, item = started()
    outcome = watchdog_outcome(request, item, now=request.watchdog_at)
    assert outcome is not None
    assert outcome.item is not None

    assert outcome.item.commitment(request.key) is not None
    done, ended = complete(outcome.request, outcome.item, now=request.watchdog_at)
    assert done.handling is HandlingState.COMPLETED
    assert ended.commitment(request.key) is None


def test_watchdog_leaves_open_work_alone_before_its_deadline() -> None:
    request, item = started()

    assert watchdog_outcome(request, item, now=T0 + timedelta(hours=2)) is None
    assert watchdog_outcome(appointment(), None, now=T0) is None


def test_watchdog_expires_work_that_never_started() -> None:
    request, item = claimed()
    deadline = request.watchdog_at

    queued = watchdog_outcome(appointment(), None, now=deadline)
    assigned = watchdog_outcome(request, item, now=deadline)

    assert queued is not None
    assert queued.request.handling is HandlingState.EXPIRED
    assert queued.item is None
    assert assigned is not None
    assert assigned.request.handling is HandlingState.EXPIRED
    assert assigned.item is not None
    assert assigned.item.state is WorkItemState.EXPIRED
    assert assigned.clear_claim


def test_watchdog_marks_started_work_for_recovery_and_keeps_the_occupation() -> None:
    request, item = started()

    outcome = watchdog_outcome(request, item, now=request.watchdog_at)

    assert outcome is not None
    assert outcome.request.handling is HandlingState.RECOVERY_REQUIRED
    assert outcome.request.resolved_at is None
    assert outcome.item is not None
    assert outcome.item.state is WorkItemState.RECOVERY_REQUIRED
    assert outcome.item.ended_at is None
    assert not outcome.clear_claim
    later = watchdog_outcome(outcome.request, outcome.item, now=request.watchdog_at + timedelta(1))
    assert later is None


def test_watchdog_cancels_unstarted_work_whose_booking_no_longer_holds_a_place() -> None:
    request, item = claimed()

    outcome = watchdog_outcome(
        request, item, phase=BookingPhase.ENDED, now=T0 + timedelta(minutes=1)
    )

    assert outcome is not None
    assert outcome.request.handling is HandlingState.CANCELLED
    assert outcome.item is not None
    assert outcome.item.state is WorkItemState.CANCELLED
    assert outcome.clear_claim


def test_a_cancelled_booking_does_not_end_treatment_that_runs() -> None:
    request, item = started()

    assert (
        watchdog_outcome(request, item, phase=BookingPhase.ENDED, now=T0 + timedelta(minutes=1))
        is None
    )


def test_watchdog_returns_work_whose_lease_ran_out_to_the_queue() -> None:
    request, item = claimed()

    outcome = watchdog_outcome(request, item, now=T0 + timedelta(minutes=5))

    assert outcome is not None
    assert outcome.request.handling is HandlingState.QUEUED
    assert outcome.item is not None
    assert outcome.item.state is WorkItemState.LEASE_EXPIRED
    assert outcome.clear_claim


def test_watchdog_leaves_a_finished_request_alone() -> None:
    done = replace(appointment(), handling=HandlingState.COMPLETED, resolved_at=T0)

    assert watchdog_outcome(done, None, phase=BookingPhase.ENDED, now=done.watchdog_at) is None


def test_the_active_work_item_states() -> None:
    assert ACTIVE_ITEM == {
        WorkItemState.ASSIGNED,
        WorkItemState.IN_PROGRESS,
        WorkItemState.RECOVERY_REQUIRED,
        WorkItemState.DISPATCHING,  # the contact-center attempts of step 6b
        WorkItemState.DISPATCHED,
        WorkItemState.RECONCILIATION_REQUIRED,
    }


def test_the_record_is_deleted_a_day_after_the_end_or_the_watchdog_whichever_is_first() -> None:
    open_request = on_demand()
    done = replace(on_demand(), handling=HandlingState.COMPLETED, resolved_at=T0)
    late = replace(
        on_demand(),
        handling=HandlingState.COMPLETED,
        resolved_at=open_request.watchdog_at + timedelta(hours=5),
    )

    assert data_deletion_at(open_request) == open_request.watchdog_at + timedelta(hours=24)
    assert data_deletion_at(done) == T0 + timedelta(hours=24)
    assert data_deletion_at(late) == open_request.watchdog_at + timedelta(hours=24)


def test_an_appointment_has_no_deletion_moment_of_its_own_and_a_request_without_one_always() -> (
    None
):
    """The booking's purge covers an appointment; without one, the pseudonyms go too (A20)."""
    deadline = on_demand().watchdog_at + timedelta(hours=24)
    assert data_deletion_at(appointment()) is None
    assert data_deletion_at(on_demand(personal_data=PersonalDataState.NOT_COLLECTED)) == deadline
    assert data_deletion_at(on_demand(personal_data=PersonalDataState.DELETED)) == deadline


def contact() -> Contact:
    """Return a participant's contact details."""
    return Contact.create(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@voorbeeld.nl",
        phone=None,
        phone_mode=PhoneMode.HIDDEN,
    )


def test_a_request_with_a_booking_reads_the_booking_and_never_the_record() -> None:
    record = RequestPersonalData(first_name="Other")

    found = contact_of(
        appointment(), booking_contact=contact(), booking_purged=False, record=record
    )
    gone = contact_of(appointment(), booking_contact=None, booking_purged=True, record=record)
    never = contact_of(appointment(), booking_contact=None, booking_purged=False, record=record)

    assert found.state is PersonalDataState.PRESENT
    assert found.data is not None
    assert (found.data.first_name, found.data.email) == ("Ada", "ada@voorbeeld.nl")
    assert gone == type(gone)(PersonalDataState.DELETED)
    assert never == type(never)(PersonalDataState.NOT_COLLECTED)


def test_a_request_without_a_booking_reads_its_record_and_invents_nothing() -> None:
    record = RequestPersonalData(first_name="Ada")

    found = contact_of(on_demand(), booking_contact=contact(), booking_purged=False, record=record)
    deleted = contact_of(
        on_demand(personal_data=PersonalDataState.DELETED),
        booking_contact=None,
        booking_purged=False,
        record=None,
    )
    missing = contact_of(
        on_demand(personal_data=PersonalDataState.NOT_COLLECTED),
        booking_contact=None,
        booking_purged=False,
        record=None,
    )

    assert found.data == record
    assert found.data is not None
    assert (found.data.email, found.data.last_name, found.data.phone) == (None, None, None)
    assert deleted.state is PersonalDataState.DELETED
    assert missing.state is PersonalDataState.NOT_COLLECTED
    assert deleted.data is missing.data is None


def test_personal_data_stays_out_of_repr() -> None:
    record = RequestPersonalData("Ada", "Lovelace", "ada@voorbeeld.nl", "+31612345678")
    _, item = claimed()

    shown = (
        repr(record)
        + repr(item)
        + repr(contact_of(on_demand(), booking_contact=None, booking_purged=False, record=record))
    )

    for secret in ("Ada", "Lovelace", "ada@voorbeeld.nl", "+31612345678", AGENT):
        assert secret not in shown
    assert RequestPersonalData().empty
    assert not record.empty


def test_a_booking_on_hold_pauses_unstarted_work_and_no_deadline_runs() -> None:
    request, item = claimed()
    late = request.watchdog_at + timedelta(days=3)

    waiting = watchdog_outcome(appointment(), None, phase=BookingPhase.PAUSED, now=late)
    lease = watchdog_outcome(request, item, phase=BookingPhase.PAUSED, now=late)

    assert waiting is None
    assert lease is not None
    assert lease.request.handling is HandlingState.QUEUED  # only the lease ends


def test_the_deadline_follows_the_booking_when_it_goes_ahead_at_another_time() -> None:
    moved = END + timedelta(days=7) + timedelta(hours=24)

    before = watchdog_outcome(appointment(), None, now=END + timedelta(days=2), deadline=moved)
    after = watchdog_outcome(appointment(), None, now=moved, deadline=moved)

    assert before is None
    assert after is not None
    assert after.request.handling is HandlingState.EXPIRED
    assert after.request.watchdog_at == moved


def test_started_work_is_marked_for_recovery_even_when_its_booking_ended() -> None:
    request, item = started()

    outcome = watchdog_outcome(request, item, phase=BookingPhase.ENDED, now=request.watchdog_at)

    assert outcome is not None
    assert outcome.request.handling is HandlingState.RECOVERY_REQUIRED


def test_the_booking_phase_of_each_status() -> None:
    assert booking_phase(None) is BookingPhase.ACTIVE
    assert booking_phase(BookingStatus.CONFIRMED) is BookingPhase.ACTIVE
    for paused in (
        BookingStatus.SUSPENDED,
        BookingStatus.AWAITING_REBOOK,
        BookingStatus.PENDING_CONFIRMATION,
    ):
        assert booking_phase(paused) is BookingPhase.PAUSED
    for ended in (
        BookingStatus.CANCELLED,
        BookingStatus.REFUSED,
        BookingStatus.COMPLETED,
        BookingStatus.NO_SHOW,
    ):
        assert booking_phase(ended) is BookingPhase.ENDED


def test_only_a_flow_without_video_that_is_not_legacy_is_request_work() -> None:
    assert is_request_work(revision(interaction_kind=InteractionKind.NONE))
    assert not is_request_work(revision(interaction_kind=InteractionKind.VIDEO))
    assert not is_request_work(legacy())
    assert not is_request_work(None)
