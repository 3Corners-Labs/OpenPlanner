"""The pure rules of immediate intake (ADR 0121 step 7): admission, view, access, lifecycle."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.contact_center import ContactCenterCapabilities
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_readiness import ReadinessCheck, ReadinessReason, readiness
from app.domain.flow_resolution import FlowRef
from app.domain.immediate_intake import (
    IntakeDecision,
    IntakeOutcome,
    RequestStatus,
    admit_on_demand,
    allows,
    new_access,
    new_intake_key,
    page_open,
    place_in_line,
    status_of,
    valid_intake_key,
)
from app.domain.pool_accounting import Commitment
from app.domain.pool_staffing import Segment, Supply
from app.domain.scheduling import Offer
from app.domain.service_flows import DispatchKind, HandlingAction, IntakeMode, InteractionKind
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkItemState,
    appointment_request,
    check_in,
    claim,
    needs_check_in,
    on_demand_request,
    start,
    withdraw,
)
from tests.support.service_flows import legacy, pool, revision

T = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    """Return 09:00 plus ``minutes``."""
    return T + timedelta(minutes=minutes)


def supply(units: int = 2, offer: Offer = Offer.BOTH) -> Supply:
    """Return one segment 09:00–12:00 of ``units`` offering ``offer``."""
    return Supply((Segment(at(0), at(180), units, offer),))


def reserved(count: int = 2) -> list[Commitment]:
    """Return ``count`` appointments 10:00–10:30 (the brief's R1 and R2)."""
    return [Commitment(("booking", n), at(60), at(90)) for n in range(1, count + 1)]


def admit(**changes: object) -> IntakeDecision:
    """Return ``admit_on_demand`` for a 20-minute request at 09:55 with the brief's pool."""
    arguments: dict[str, object] = {
        "now": at(55),
        "duration_minutes": 20,
        "buffer_minutes": 0,
        "waiting": 0,
        "max_waiting": None,
        "lookahead_minutes": 120,
    }
    arguments.update(changes)
    found_supply = arguments.pop("supply", supply())
    commitments = arguments.pop("commitments", reserved())
    return admit_on_demand(found_supply, commitments, **arguments)  # type: ignore[arg-type]


def test_the_briefs_example_is_accepted_with_the_forecast_after_the_reservations() -> None:
    """A17: a ticket may be issued at 09:55; the expected start is 10:30, not 09:55."""
    decision = admit()

    assert decision.outcome is IntakeOutcome.ACCEPTED
    assert decision.expected_start == at(90)


def test_the_waiting_limit_refuses_and_is_checked_first() -> None:
    assert admit(waiting=3, max_waiting=3).outcome is IntakeOutcome.FULL
    assert admit(waiting=2, max_waiting=3).outcome is IntakeOutcome.ACCEPTED


def test_no_start_within_the_look_ahead_is_unavailable() -> None:
    """The reservations end at 10:30: a look-ahead of 30 minutes from 09:55 does not reach it."""
    assert admit(lookahead_minutes=30).outcome is IntakeOutcome.UNAVAILABLE
    assert admit(lookahead_minutes=35).expected_start == at(90)


def test_a_block_for_appointments_only_takes_no_immediate_work() -> None:
    """P7: only units of a block that offers work without appointment count."""
    only_appointments = supply(offer=Offer.SCHEDULED)
    assert admit(supply=only_appointments, commitments=[]).outcome is IntakeOutcome.UNAVAILABLE


def test_status_words_never_name_an_internal_state() -> None:
    assert {status_of(_request(handling=h)) for h in HandlingState if h not in _TERMINALS} == {
        RequestStatus.WAITING,
        RequestStatus.CALLED,
        RequestStatus.IN_PROGRESS,
    }
    done = _request(handling=HandlingState.COMPLETED, resolved_at=at(10))
    assert status_of(done) is RequestStatus.DONE


def test_the_place_in_line_is_first_come_first_served() -> None:
    first = replace(_request(), id=1, created_at=at(0))
    second = replace(_request(), id=2, created_at=at(1))
    claimed = replace(_request(), id=3, created_at=at(-5), handling=HandlingState.ASSIGNED)

    line = [second, claimed, first]
    assert place_in_line(line, first) == 1
    assert place_in_line(line, second) == 2
    assert place_in_line(line, claimed) is None


def test_the_page_needs_the_cookie_and_closes_with_the_data() -> None:
    """A31: the reference names, the cookie authorises; the same answer for every refusal."""
    access, value = new_access(new_intake_key())
    request = replace(_request(), access_hash=access.access_hash)

    assert allows(request, value, at(0))
    assert not allows(request, "x" * 43, at(0))
    assert not allows(request, None, at(0))
    assert not allows(None, value, at(0))
    done = replace(request, handling=HandlingState.COMPLETED, resolved_at=at(10))
    assert page_open(done, at(10) + timedelta(hours=23))
    assert not allows(done, value, at(10) + timedelta(hours=24))


def test_an_intake_key_is_checked_before_it_is_hashed() -> None:
    assert valid_intake_key(new_intake_key())
    assert not valid_intake_key(None)
    assert not valid_intake_key("short")
    assert not valid_intake_key("x" * 65)
    assert not valid_intake_key("é" * 40)


def test_a_new_access_keeps_hashes_only() -> None:
    access, value = new_access("k" * 43)
    assert value not in (access.access_hash, access.intake_key_hash)
    assert "k" * 43 not in repr(access)
    assert len(access.public_ref) <= 32


# --- the lifecycle additions of step 7 ---------------------------------------------------------


def test_immediate_work_names_its_pool_and_an_appointment_does_not() -> None:
    with pytest.raises(DomainError) as error:
        replace(_request(), pool_id=None)
    assert error.value.field == "pool_id"
    with pytest.raises(DomainError):
        replace(_appointment(), pool_id=3)


def test_the_claim_of_immediate_work_holds_a_unit_from_the_claim() -> None:
    request = replace(_request(), id=5)
    with pytest.raises(DomainError) as error:
        claim(request, None, now=at(0), agent="a", lease_minutes=5)
    assert error.value.field == "planned_until"

    _, item = claim(request, None, now=at(0), agent="a", lease_minutes=5, planned_until=at(20))
    assert item.commitment(request.key) == Commitment(("request", 5), at(0), at(20))

    _, started = start(request, item, now=at(2), pool_id=3, planned_until=at(22), conflict=False)
    assert started.commitment(request.key) == Commitment(
        ("request", 5), at(2), at(22), in_progress=True
    )


def test_an_appointments_claim_holds_no_unit_of_its_own() -> None:
    request = replace(_appointment(), id=6)
    with pytest.raises(DomainError):
        claim(request, None, now=at(0), agent="a", lease_minutes=5, planned_until=at(20))
    _, item = claim(request, None, now=at(0), agent="a", lease_minutes=5)
    assert item.commitment(request.key) is None


def test_a_double_check_in_keeps_the_first_arrival() -> None:
    """A15: the second check-in changes nothing."""
    first = check_in(_appointment(), at(0))
    assert first.arrived_at == at(0)
    assert check_in(first, at(5)) is first
    with pytest.raises(DomainError):
        check_in(_request(), at(0))  # immediate work arrives with its acceptance


def test_withdrawing_ends_a_claim_and_is_idempotent() -> None:
    request = replace(_request(), id=5)
    assigned, item = claim(
        request, None, now=at(0), agent="a", lease_minutes=5, planned_until=at(20)
    )
    gone, ended = withdraw(assigned, item, now=at(1))

    assert gone.handling is HandlingState.CANCELLED
    assert ended is not None
    assert ended.state is WorkItemState.CANCELLED
    assert ended.commitment(request.key) is None  # the unit is released
    assert withdraw(gone, None, now=at(2)) == (gone, None)
    _, started = start(assigned, item, now=at(1), pool_id=3, planned_until=at(21), conflict=False)
    with pytest.raises(DomainError) as error:
        withdraw(assigned, started, now=at(2))
    assert error.value.code is ErrorCode.REQUEST_IN_PROGRESS


def test_only_an_explicit_check_in_flow_needs_a_check_in() -> None:
    assert needs_check_in(revision(interaction_kind=InteractionKind.IN_PERSON))
    assert not needs_check_in(revision(interaction_kind=InteractionKind.TELEPHONE))
    assert not needs_check_in(revision(interaction_kind=InteractionKind.NONE))
    assert not needs_check_in(legacy())
    assert not needs_check_in(None)


_TERMINALS = {HandlingState.COMPLETED, HandlingState.CANCELLED, HandlingState.EXPIRED}


def _request(**changes: object) -> ServiceRequest:
    """Return immediate work taken in at 09:00."""
    access, _ = new_access("k" * 43)
    base = on_demand_request(
        organisation_id=1,
        group_id=1,
        flow=FlowRef(60, 1),
        pool_id=3,
        subject=None,
        access=access,
        now=at(0),
        unresolved_hours=24,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _appointment() -> ServiceRequest:
    """Return the request of an appointment ending at 09:30."""
    return appointment_request(
        organisation_id=1,
        group_id=1,
        flow=FlowRef(61, 1),
        booking_id=9,
        booking_end=at(30),
        now=at(0),
        unresolved_hours=24,
    )


def test_immediate_work_is_not_sent_to_the_contact_center() -> None:
    """Choice I8: an on-demand flow dispatched to the contact center is not built yet."""
    carrier = ContactCenterCapabilities(frozenset({InteractionKind.NONE}))
    both = frozenset({IntakeMode.APPOINTMENT, IntakeMode.ON_DEMAND})

    def dispatch(**changes: object) -> ReadinessReason | None:
        made = revision(
            interaction_kind=InteractionKind.NONE,
            handling_action=HandlingAction.VIEW_REQUEST,
            **changes,
        )
        items = readiness(made, pool(), offered_subjects=(), carrier=carrier)
        return next(i for i in items if i.code is ReadinessCheck.DISPATCH).reason

    assert dispatch(intake_modes=both, dispatch=DispatchKind.CONTACT_CENTER) is (
        ReadinessReason.NOT_BUILT_YET
    )
    assert dispatch(dispatch=DispatchKind.CONTACT_CENTER) is None
    assert dispatch(intake_modes=both, dispatch=DispatchKind.OWN_QUEUE) is None
