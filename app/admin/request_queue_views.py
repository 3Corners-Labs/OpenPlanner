"""Immediate work as the queue page shows it (ADR 0121 step 7): rows and the claimed request.

Written out for the templates from ``RequestQueue``'s answers: the first name only, the wait in
the service's clock, the state in words (colour is never the only signal), the skills an agent
lacks named (choice S3) and the one action a row allows.
"""

from dataclasses import dataclass

from fastapi import Request

from app.admin.authz import AdminContext
from app.admin.skill_inputs import skill_name
from app.domain.request_queue import ClaimedRequest, ImmediateRow, RequestQueue
from app.domain.service_requests import HandlingState, WorkItemState
from app.i18n.dates import format_clock
from app.web.locale import RequestLocale

STATE_LABELS: dict[HandlingState, tuple[str, str]] = {
    HandlingState.QUEUED: ("admin.queue.request.waiting", "warning"),
    HandlingState.ASSIGNED: ("admin.queue.state.claimed", ""),
    HandlingState.IN_PROGRESS: ("admin.queue.request.in_progress", "success"),
    HandlingState.RECOVERY_REQUIRED: ("admin.queue.request.recovery", "danger"),
}
"""Catalogue key and chip tone per state of open immediate work."""


@dataclass(frozen=True)
class ImmediateRowView:
    """One request without an appointment, written out for the queue's second table."""

    id: int
    since: str
    first_name: str
    state: str
    tone: str
    mine: bool
    eligible: bool
    missing: str
    can_claim: bool
    can_open: bool
    waiting: bool


@dataclass(frozen=True)
class ClaimedRequestView:
    """The claimed request's page: what the holder sees and may do."""

    request_id: int
    first_name: str
    since: str
    sentence: str
    can_start: bool
    can_complete: bool
    claimed: ClaimedRequest


def queue(request: Request) -> RequestQueue:
    """Return the application's immediate work."""
    found: RequestQueue = request.app.state.request_queue
    return found


def _row(
    row: ImmediateRow, locale: RequestLocale, context: AdminContext, free: bool
) -> ImmediateRowView:
    """Write one immediate request out."""
    label, tone = STATE_LABELS.get(row.handling, ("admin.queue.state.claimed", ""))
    if row.mine:
        label, tone = "admin.queue.state.mine", "success"
    return ImmediateRowView(
        id=row.request_id,
        since=format_clock(row.since, context.group.group.tz, locale.code),
        first_name=row.first_name,
        state=locale.gettext(label),
        tone=tone,
        mine=row.mine,
        eligible=row.eligible,
        missing=", ".join(skill_name(skill, locale.code) for skill in row.missing),
        can_claim=free and not row.claimed and row.eligible,
        can_open=row.mine and row.eligible,
        waiting=row.handling is HandlingState.QUEUED and not row.claimed,
    )


async def immediate_rows(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str, free: bool
) -> list[ImmediateRowView]:
    """Return the service's open immediate requests as the queue page lists them.

    A row offers *Claim* only where the service allows free picking (``free``); otherwise
    *Pick up next* takes the oldest one the agent may take (choice I9). The claim judges the
    pool, so a request that would take a reserved unit is refused with the reason, never hidden.
    """
    rows = await queue(request).view(context.principal, slug)
    return [_row(r, locale, context, free) for r in rows]


def has_next(rows: list[ImmediateRowView]) -> bool:
    """Return whether *Pick up next* has a request to take: one waiting that the agent may take."""
    return any(r.waiting and r.eligible for r in rows)


def claimed_view(
    claimed: ClaimedRequest, locale: RequestLocale, context: AdminContext
) -> ClaimedRequestView:
    """Write the claimed request out: a sentence for its state, and the action it allows."""
    tz = context.group.group.tz
    item = claimed.item
    request = claimed.request
    if item is not None and item.state is WorkItemState.ASSIGNED and item.lease_until:
        sentence = locale.gettext(
            "admin.queue.work.assigned", time=format_clock(item.lease_until, tz, locale.code)
        )
    elif item is not None and item.started_at and request.handling is HandlingState.IN_PROGRESS:
        sentence = locale.gettext(
            "admin.queue.work.in_progress", time=format_clock(item.started_at, tz, locale.code)
        )
    elif request.handling is HandlingState.RECOVERY_REQUIRED:
        sentence = locale.gettext("admin.queue.work.recovery")
    else:
        sentence = ""
    return ClaimedRequestView(
        request_id=request.id or 0,
        first_name=claimed.first_name,
        since=format_clock(request.arrived_at or request.created_at, tz, locale.code),
        sentence=sentence,
        can_start=item is not None and item.state is WorkItemState.ASSIGNED,
        can_complete=item is not None and item.started and item.active,
        claimed=claimed,
    )
