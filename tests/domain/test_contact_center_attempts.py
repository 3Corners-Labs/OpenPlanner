"""The pure lifecycle of a contact-center attempt (ADR 0121 step 6b), and its readiness.

One active work item per request is the fence; an attempt opens only when nobody holds the
request, an attempt in doubt is adopted or fenced, never both; a contact-center flow completes by
the platform's event and an own-queue flow by the operator.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain import contact_center_attempts as attempts
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_readiness import ReadinessCheck, ReadinessReason, readiness
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    HandlingAction,
    InteractionKind,
)
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkChannel,
    WorkItem,
    WorkItemState,
    appointment_request,
    claim,
)
from tests.support.contact_center_requests import CC_FLOW
from tests.support.domain import NOW, webex_routing
from tests.support.service_flows import pool, revision

LATER = NOW + timedelta(minutes=10)


def queued() -> ServiceRequest:
    """Return a queued request of an appointment."""
    made = appointment_request(
        organisation_id=1,
        group_id=1,
        flow=CC_FLOW,
        booking_id=7,
        booking_end=NOW + timedelta(minutes=30),
        now=NOW,
        unresolved_hours=24,
    )
    return replace(made, id=3)


def opened() -> tuple[ServiceRequest, WorkItem]:
    """Return a request with an open attempt, stored."""
    request, item = attempts.open_attempt(queued(), None, now=NOW)
    return request, replace(item, id=9)


def code(call: object) -> ErrorCode:
    with pytest.raises(DomainError) as excinfo:
        call()  # type: ignore[operator]
    return excinfo.value.code


def test_an_attempt_opens_only_when_nobody_holds_the_request() -> None:
    request, item = opened()
    assert (request.handling, item.state, item.channel) == (
        HandlingState.ASSIGNED,
        WorkItemState.DISPATCHING,
        WorkChannel.CONTACT_CENTER,
    )
    assert item.assigned_to is None
    assert item.lease_until is None
    assert code(lambda: attempts.open_attempt(request, item, now=NOW)) is ErrorCode.ALREADY_CLAIMED
    _, own = claim(queued(), None, now=NOW, agent="a", lease_minutes=5)
    assert code(lambda: attempts.open_attempt(queued(), own, now=NOW)) is ErrorCode.ALREADY_CLAIMED
    done = replace(queued(), handling=HandlingState.COMPLETED, resolved_at=NOW)
    assert code(lambda: attempts.open_attempt(done, None, now=NOW)) is ErrorCode.BOOKING_NOT_ACTIVE


def test_only_an_attempt_in_doubt_is_adopted_or_fenced() -> None:
    request, item = opened()
    doubt = attempts.in_doubt(item)
    assert doubt.state is WorkItemState.RECONCILIATION_REQUIRED
    assert attempts.dispatched(item).state is WorkItemState.DISPATCHED
    assert attempts.dispatched(doubt).state is WorkItemState.DISPATCHED
    sent = attempts.dispatched(item)
    assert code(lambda: attempts.in_doubt(sent)) is ErrorCode.NOT_CLAIMED
    assert code(lambda: attempts.dispatched(sent)) is ErrorCode.NOT_CLAIMED
    assert code(lambda: attempts.fence(request, sent, now=LATER)) is ErrorCode.NOT_CLAIMED
    back, fenced = attempts.fence(request, doubt, now=LATER)
    assert (back.handling, back.resolved_at) == (HandlingState.QUEUED, None)
    assert (fenced.state, fenced.ended_at, fenced.active) == (WorkItemState.FENCED, LATER, False)


def test_the_own_queues_work_is_never_an_attempt() -> None:
    _, own = claim(queued(), None, now=NOW, agent="a", lease_minutes=5)
    for change in (attempts.dispatched, attempts.in_doubt):
        assert code(lambda change=change: change(own)) is ErrorCode.NOT_CLAIMED
    assert code(lambda: attempts.ended(queued(), own, now=LATER)) is ErrorCode.NOT_CLAIMED
    assert code(lambda: attempts.failed(queued(), None, now=LATER)) is ErrorCode.NOT_CLAIMED
    assert not attempts.due_for_fence(own, LATER + timedelta(days=1), 10)


def test_the_fence_waits_for_the_window() -> None:
    _, item = opened()
    doubt = attempts.in_doubt(item)
    assert not attempts.due_for_fence(doubt, NOW + timedelta(minutes=9), 10)
    assert attempts.due_for_fence(doubt, NOW + timedelta(minutes=10), 10)
    assert attempts.due_for_fence(item, NOW + timedelta(minutes=10), 10)  # a crash mid-create
    assert not attempts.due_for_fence(attempts.dispatched(item), NOW + timedelta(hours=1), 10)


def test_platform_events_start_complete_or_requeue() -> None:
    request, item = opened()
    sent = attempts.dispatched(item)
    assert (
        code(
            lambda: attempts.connected(
                request, item, now=LATER, pool_id=1, planned_until=LATER, conflict=False
            )
        )
        is ErrorCode.NOT_CLAIMED
    )
    running_request, running = attempts.connected(
        request,
        sent,
        now=LATER,
        pool_id=4,
        planned_until=LATER + timedelta(minutes=35),
        conflict=True,
    )
    assert running_request.handling is HandlingState.IN_PROGRESS
    assert (running.state, running.started_at, running.pool_id, running.start_conflict) == (
        WorkItemState.IN_PROGRESS,
        LATER,
        4,
        True,
    )
    done_request, done = attempts.ended(running_request, running, now=LATER)
    assert (done_request.handling, done_request.resolved_at) == (HandlingState.COMPLETED, LATER)
    assert (done.state, done.ended_at) == (WorkItemState.COMPLETED, LATER)
    back, failed = attempts.failed(request, sent, now=LATER)
    assert back.handling is HandlingState.QUEUED
    assert (failed.state, failed.ended_at) == (WorkItemState.FAILED, LATER)


def _completion(dispatch: DispatchKind, completion: CompletionPolicy) -> ReadinessReason | None:
    made = revision(
        dispatch=dispatch,
        completion=completion,
        interaction_kind=InteractionKind.NONE,
        handling_action=HandlingAction.VIEW_REQUEST,
    )
    carrier = webex_routing().descriptor.capabilities
    items = readiness(made, pool(), offered_subjects=(), carrier=carrier)
    return next(i for i in items if i.code is ReadinessCheck.COMPLETION).reason


def test_each_dispatch_has_its_own_completion() -> None:
    cc, own = DispatchKind.CONTACT_CENTER, DispatchKind.OWN_QUEUE
    event, operator = CompletionPolicy.PROVIDER_EVENT, CompletionPolicy.OPERATOR
    assert _completion(cc, event) is None
    assert _completion(own, operator) is None
    assert _completion(cc, operator) is ReadinessReason.COMPLETION_NOT_FOR_DISPATCH
    assert _completion(own, event) is ReadinessReason.COMPLETION_NOT_FOR_DISPATCH


def test_a_contact_center_flow_without_a_meeting_waits_only_for_the_rollback_gate() -> None:
    made = revision(
        dispatch=DispatchKind.CONTACT_CENTER,
        completion=CompletionPolicy.PROVIDER_EVENT,
        interaction_kind=InteractionKind.NONE,
        handling_action=HandlingAction.VIEW_REQUEST,
    )
    carrier = webex_routing().descriptor.capabilities

    items = readiness(made, pool(), offered_subjects=(), carrier=carrier)

    assert [(i.code, i.reason) for i in items if not i.ok] == [
        (ReadinessCheck.ROLLBACK, ReadinessReason.ROLLBACK_NOT_SAFE)
    ]
