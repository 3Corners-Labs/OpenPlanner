"""The own queue of a group (ADR 0043): agents see waiting bookings and pick one up.

Every route needs ``queue.work`` on the group; a group that dispatches to the contact center
has no queue (404). The list shows the first name only. The meeting link appears only on the
page of a booking the agent claimed. Without JavaScript the list is refreshed with the
"Refresh" link; new and waiting rows are announced in a polite live region on load.

    GET  /admin/groups/{slug}/queue                         queue.work
    POST /admin/groups/{slug}/queue/next                    queue.work  (pick up next)
    POST /admin/groups/{slug}/queue/{booking_id}/claim      queue.work  (free pick only)
    GET  /admin/groups/{slug}/queue/{booking_id}            queue.work  (claimer only)
    POST /admin/groups/{slug}/queue/{booking_id}/release    queue.work  (claimer, group admin)
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import tzinfo
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import raise_refusal, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.queue_service import ClaimedBooking, QueueRow, QueueService, QueueView
from app.i18n import BoundGettext
from app.i18n.dates import format_clock
from app.web.errors import ERROR_KEYS
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

WorkQueue = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.QUEUE_WORK, group_param="slug")),
]

SHOWN_ON_THE_LIST = frozenset(
    {
        ErrorCode.QUEUE_EMPTY,
        ErrorCode.ALREADY_CLAIMED,
        ErrorCode.FREE_PICK_NOT_ALLOWED,
        ErrorCode.NOT_CLAIMED,
        ErrorCode.BOOKING_NOT_ACTIVE,
    }
)
"""Refusals that are answered with the list and a message, not an error page."""


@dataclass(frozen=True)
class RowView:
    """One row of the queue, written out."""

    id: int
    time: str
    first_name: str
    reference: str
    state: str
    tone: str
    direct: bool
    overdue: bool
    mine: bool
    can_claim: bool
    can_release: bool


def _service(request: Request) -> QueueService:
    """Return the application's queue service."""
    service: QueueService = request.app.state.queue_service
    return service


def _url(slug: str) -> str:
    """Return the queue's address."""
    return f"/admin/groups/{slug}/queue"


def _state(row: QueueRow, _: BoundGettext) -> tuple[str, str]:
    """Return the row's state in words and its colour tone (never the only signal)."""
    if row.mine:
        return _("admin.queue.state.mine"), "success"
    if row.claimed:
        return _("admin.queue.state.claimed"), ""
    if row.in_lobby:
        return _("admin.queue.state.in_lobby"), "warning"
    return _("admin.queue.state.upcoming"), ""


def _row(row: QueueRow, view: QueueView, locale: RequestLocale, tz: tzinfo) -> RowView:
    """Write one queue row out for the template."""
    label, tone = _state(row, locale.gettext)
    return RowView(
        id=row.booking_id,
        time=format_clock(row.start, tz, locale.code),
        first_name=row.first_name,
        reference=row.reference,
        state=label,
        tone=tone,
        direct=row.direct,
        overdue=row.overdue,
        mine=row.mine,
        can_claim=view.free_pick and not row.claimed,
        can_release=view.can_release_any and row.claimed and not row.mine,
    )


async def _list(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    refusal: ErrorCode | None = None,
) -> HTMLResponse:
    """Render the queue, with a refusal message when a pickup did not work out."""
    _ = locale.gettext
    view = await _service(request).view(context.principal, slug)
    rows = [_row(r, view, locale, view.group.tz) for r in view.rows]
    waiting = sum(1 for r in view.rows if r.in_lobby and not r.claimed)
    return group_page(
        request,
        locale,
        context,
        "admin/group_queue.html",
        {
            "rows": rows,
            "has_next": view.next_booking_id is not None,
            "free_pick": view.free_pick,
            "announcement": _("admin.queue.announcement", count=len(rows), waiting=waiting),
            "refusal": _(ERROR_KEYS[refusal]) if refusal else None,
        },
        409 if refusal else 200,
    )


async def _claimed(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    attempt: Awaitable[ClaimedBooking],
) -> Response:
    """Go to the claimed booking, or back to the list with the reason it did not work."""
    try:
        claimed = await attempt
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await _list(request, locale, context, slug, error.code)
    return RedirectResponse(f"{_url(slug)}/{claimed.booking_id}", status_code=303)


@router.get("/groups/{slug}/queue", response_class=HTMLResponse)
async def queue(request: Request, locale: Locale, slug: str, context: WorkQueue) -> HTMLResponse:
    """Show the group's own queue."""
    return await _list(request, locale, context, slug)


@router.post("/groups/{slug}/queue/next", response_class=HTMLResponse)
async def pick_up_next(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim the booking that is next and open it."""
    attempt = _service(request).claim_next(context.principal, slug)
    return await _claimed(request, locale, context, slug, attempt)


@router.post("/groups/{slug}/queue/{booking_id}/claim", response_class=HTMLResponse)
async def claim(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim one chosen booking (only where the group allows free picking)."""
    attempt = _service(request).claim(context.principal, slug, booking_id)
    return await _claimed(request, locale, context, slug, attempt)


@router.get("/groups/{slug}/queue/{booking_id}", response_class=HTMLResponse)
async def claimed(
    request: Request, locale: Locale, slug: str, booking_id: int, context: WorkQueue
) -> Response:
    """Show a booking the agent claimed, with the link to join its meeting."""
    try:
        booking = await _service(request).claimed(context.principal, slug, booking_id)
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await _list(request, locale, context, slug, error.code)
    tz = context.group.group.tz
    return group_page(
        request,
        locale,
        context,
        "admin/queue_claimed.html",
        {"booking": booking, "time": format_clock(booking.start, tz, locale.code)},
    )


@router.post("/groups/{slug}/queue/{booking_id}/release", response_class=HTMLResponse)
async def release(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Put a claimed booking back in the queue."""
    try:
        await _service(request).release(context.principal, slug, booking_id)
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await _list(request, locale, context, slug, error.code)
    return redirect_done(_url(slug), "released")
