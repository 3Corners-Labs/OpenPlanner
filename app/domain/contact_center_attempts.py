"""A contact-center attempt on a request: the pure lifecycle (ADR 0121 step 6b).

The work of a request whose flow dispatches to the contact center is handed to the platform as
a work item of channel ``contact_center``: one attempt. One active work item per request is the
fence between the own queue and the contact center (``uq_work_items_active``), so an attempt
opens only when no one holds the request, and a claim in the own queue is refused while an
attempt is active.

    open ─► dispatching ─created─► dispatched ─connected─► in_progress ─ended─► completed
               │   └─refused / not sent─► failed (request queued)    └─failed─► failed
               └─outcome unknown─► reconciliation_required ─late task─► dispatched
                                         └─window over─► fenced (request queued, own queue)

Pure Python, no I/O; the use cases are in ``app.domain.request_dispatch``. The build brief is
``docs/service-flows-contact-center-contract-design.md`` section 11.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from app.domain.errors import DomainError, ErrorCode
from app.domain.service_requests import (
    IN_DOUBT,
    HandlingState,
    ServiceRequest,
    WorkChannel,
    WorkItem,
    WorkItemState,
)


def _handled(request: ServiceRequest, handling: HandlingState, now: datetime) -> ServiceRequest:
    """Return ``request`` in ``handling``; ``resolved_at`` set only for a completed request."""
    resolved = now if handling is HandlingState.COMPLETED else None
    return replace(request, handling=handling, resolved_at=resolved)


def _attempt(item: WorkItem | None) -> WorkItem:
    """Return ``item`` when it is an active contact-center attempt.

    Raises:
        DomainError: ``not_claimed`` otherwise.
    """
    if item is None or not item.active or item.channel is not WorkChannel.CONTACT_CENTER:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    return item


def open_attempt(
    request: ServiceRequest, active: WorkItem | None, *, now: datetime
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request assigned to the contact center and its new attempt, ``dispatching``.

    Raises:
        DomainError: ``booking_not_active`` for a finished request or one in recovery,
            ``already_claimed`` when someone holds it (the fence).
    """
    if request.terminal or request.handling is HandlingState.RECOVERY_REQUIRED:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    if active is not None or request.handling is not HandlingState.QUEUED:
        raise DomainError(ErrorCode.ALREADY_CLAIMED)
    item = WorkItem(
        id=None,
        request_id=request.id or 0,
        state=WorkItemState.DISPATCHING,
        assigned_to=None,
        assigned_at=now,
        lease_until=None,
        channel=WorkChannel.CONTACT_CENTER,
    )
    return _handled(request, HandlingState.ASSIGNED, now), item


def dispatched(item: WorkItem | None) -> WorkItem:
    """Return the attempt held by the platform: its task exists (created, or found late).

    Raises:
        DomainError: ``not_claimed`` for an attempt that is not in doubt.
    """
    attempt = _attempt(item)
    if attempt.state not in IN_DOUBT:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    return replace(attempt, state=WorkItemState.DISPATCHED)


def in_doubt(item: WorkItem | None) -> WorkItem:
    """Return the attempt whose create had an unknown outcome: ``reconciliation_required``.

    Raises:
        DomainError: ``not_claimed`` unless it is ``dispatching``.
    """
    attempt = _attempt(item)
    if attempt.state is not WorkItemState.DISPATCHING:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    return replace(attempt, state=WorkItemState.RECONCILIATION_REQUIRED)


def failed(
    request: ServiceRequest, item: WorkItem | None, *, now: datetime
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request queued again and the attempt ended ``failed``.

    A started attempt's unit is released with it (``ended_at``).

    Raises:
        DomainError: ``not_claimed`` without an active attempt.
    """
    attempt = _attempt(item)
    ended = replace(attempt, state=WorkItemState.FAILED, ended_at=now)
    return _handled(request, HandlingState.QUEUED, now), ended


def due_for_fence(item: WorkItem, now: datetime, window_minutes: int) -> bool:
    """Return whether an attempt in doubt has waited ``window_minutes`` since it opened."""
    return (
        item.channel is WorkChannel.CONTACT_CENTER
        and item.state in IN_DOUBT
        and item.ended_at is None
        and item.assigned_at + timedelta(minutes=window_minutes) <= now
    )


def fence(
    request: ServiceRequest, item: WorkItem | None, *, now: datetime
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request queued for the own queue and the attempt in doubt ended ``fenced``.

    From then on one authority holds the request: the own queue. A task the platform made after
    all is ended when it shows up (``app.domain.request_dispatch``).

    Raises:
        DomainError: ``not_claimed`` unless the attempt is in doubt.
    """
    attempt = _attempt(item)
    if attempt.state not in IN_DOUBT:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    ended = replace(attempt, state=WorkItemState.FENCED, ended_at=now)
    return _handled(request, HandlingState.QUEUED, now), ended


def connected(
    request: ServiceRequest,
    item: WorkItem | None,
    *,
    now: datetime,
    pool_id: int,
    planned_until: datetime,
    conflict: bool,
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request and its attempt in treatment: the platform connected an agent.

    The attempt occupies a unit of ``pool_id`` until ``planned_until``. The call is already
    running, so an overrun start is recorded (``conflict``), never refused.

    Raises:
        DomainError: ``not_claimed`` unless the attempt is ``dispatched``.
    """
    attempt = _attempt(item)
    if attempt.state is not WorkItemState.DISPATCHED:
        raise DomainError(ErrorCode.NOT_CLAIMED)
    started = replace(
        attempt,
        state=WorkItemState.IN_PROGRESS,
        started_at=now,
        pool_id=pool_id,
        planned_until=planned_until,
        start_conflict=conflict,
    )
    return _handled(request, HandlingState.IN_PROGRESS, now), started


def ended(
    request: ServiceRequest, item: WorkItem | None, *, now: datetime
) -> tuple[ServiceRequest, WorkItem]:
    """Return the request completed and its attempt ended: the platform ended the task.

    An attempt that never connected is completed too: the platform says the work is done.

    Raises:
        DomainError: ``not_claimed`` without an active attempt.
    """
    attempt = _attempt(item)
    done = replace(attempt, state=WorkItemState.COMPLETED, ended_at=now)
    return _handled(request, HandlingState.COMPLETED, now), done
