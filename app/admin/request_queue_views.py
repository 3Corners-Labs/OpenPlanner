"""Immediate work as the queue page shows it (ADR 0121 step 7): rows and the claimed request.

Written out for the templates from ``RequestQueue``'s answers: the first name only, the wait in
the service's clock, the state in words (colour is never the only signal), the skills an agent
lacks named (choice S3) and the one action a row allows.
"""

from dataclasses import dataclass
from datetime import tzinfo

from fastapi import Request

from app.admin.authz import AdminContext
from app.admin.pages import admin_moment
from app.admin.skill_inputs import skill_name
from app.domain.request_queue import ClaimedRequest, ImmediateRow, RequestQueue
from app.domain.request_recovery import RecoveryWork
from app.domain.service_requests import HandlingState, WorkItem, WorkItemState
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


@dataclass(frozen=True)
class WorkView:
    """Request work on a claimed page: where it stands and what may be done (ADR 0121)."""

    state: str
    sentence: str
    can_start: bool
    can_complete: bool
    conflict: bool
    recovery: bool


@dataclass(frozen=True)
class ClaimedRequestView:
    """The claimed request's page: what the holder sees and may do."""

    request_id: int
    first_name: str
    since: str
    work: WorkView
    claimed: ClaimedRequest


@dataclass(frozen=True)
class ImmediateList:
    """The immediate rows of the queue page and whether *Pick up next* has one to take."""

    rows: list[ImmediateRowView]
    has_next: bool


@dataclass(frozen=True)
class RecoveryRowView:
    """One request in recovery, written out for the supervisors' table (ADR 0121 step 5e).

    Attributes:
        work: The appointment's reference, or the words for work without one.
        started: When treatment started, in the admin's clock.
        deadline: When the deadline passed, in the admin's clock.
        complete_path: Where *Complete* posts: the appointment's or the request's own route.
        key: What the row shows, for the live fragment's key.
    """

    work: str
    started: str
    deadline: str
    complete_path: str

    @property
    def key(self) -> tuple[str, str, str]:
        """Return what changes the row."""
        return (self.complete_path, self.started, self.deadline)


def recovery_rows(
    request: Request, locale: RequestLocale, slug: str, recovery: tuple[RecoveryWork, ...]
) -> list[RecoveryRowView]:
    """Write a service's work in recovery out; *Complete* uses the queue's existing routes."""
    queue_path = f"/admin/groups/{slug}/queue"
    rows = []
    for work in recovery:
        path = (
            f"{queue_path}/{work.booking_id}/complete"
            if work.booking_id is not None
            else f"{queue_path}/requests/{work.request_id}/complete"
        )
        rows.append(
            RecoveryRowView(
                work.reference or locale.gettext("admin.queue.recovery.without_appointment"),
                admin_moment(request, locale, work.started_at) if work.started_at else "–",
                admin_moment(request, locale, work.watchdog_at),
                path,
            )
        )
    return rows


def queue(request: Request) -> RequestQueue:
    """Return the application's immediate work."""
    found: RequestQueue = request.app.state.request_queue
    return found


def _row(
    row: ImmediateRow, locale: RequestLocale, context: AdminContext, free: bool
) -> ImmediateRowView:
    """Write one immediate request out."""
    label, tone = STATE_LABELS[row.handling]
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
    )


async def immediate_rows(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str, free: bool
) -> ImmediateList:
    """Return the service's open immediate requests as the queue page lists them.

    A row offers *Claim* only where the service allows free picking (``free``); otherwise
    *Pick up next* takes the one the domain names (choice I9). The claim judges the pool, so a
    request that would take a reserved unit is refused with the reason, never hidden.
    """
    found = await queue(request).view(context.principal, slug)
    rows = [_row(r, locale, context, free) for r in found.rows]
    return ImmediateList(rows, found.next_request_id is not None)


def work_view(
    item: WorkItem | None, handling: HandlingState, locale: RequestLocale, tz: tzinfo
) -> WorkView:
    """Write request work out: a sentence for its state, and the one action it allows.

    The one rule for an appointment's request (ADR 0121 step 5) and immediate work (step 7).
    """
    _ = locale.gettext
    if item is not None and item.state is WorkItemState.ASSIGNED and item.lease_until:
        sentence = _(
            "admin.queue.work.assigned", time=format_clock(item.lease_until, tz, locale.code)
        )
    elif item is not None and item.started_at and handling is HandlingState.IN_PROGRESS:
        sentence = _(
            "admin.queue.work.in_progress", time=format_clock(item.started_at, tz, locale.code)
        )
    elif handling is HandlingState.RECOVERY_REQUIRED:
        sentence = _("admin.queue.work.recovery")
    else:
        sentence = ""
    return WorkView(
        state=handling.value,
        sentence=sentence,
        can_start=item is not None and item.state is WorkItemState.ASSIGNED,
        can_complete=item is not None and item.started and item.active,
        conflict=item is not None and item.start_conflict,
        recovery=handling is HandlingState.RECOVERY_REQUIRED,
    )


def claimed_view(
    claimed: ClaimedRequest, locale: RequestLocale, context: AdminContext
) -> ClaimedRequestView:
    """Write the claimed request out: its first name, since when, and its work."""
    tz = context.group.group.tz
    request = claimed.request
    return ClaimedRequestView(
        request_id=request.id or 0,
        first_name=claimed.first_name,
        since=format_clock(request.arrived_at or request.created_at, tz, locale.code),
        work=work_view(claimed.item, request.handling, locale, tz),
        claimed=claimed,
    )
