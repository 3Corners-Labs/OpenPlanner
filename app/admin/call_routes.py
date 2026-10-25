"""Admin views of calls (ADR 0010): a group's agenda per day and a booking's timeline.

Both need ``bookings.view`` on the group. The agenda shows the first name only (no email or
phone), the meeting and work item state in words, whether the agent joined, the number of
participants and a "not picked up" flag. The timeline lists every recorded event with its
reason code; nothing about participants beyond counts.

    GET /admin/groups/{slug}/agenda?day=YYYY-MM-DD       bookings.view
    GET /admin/groups/{slug}/bookings/{booking_id}       bookings.view
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AccessDenied, AdminContext, RequirePermission
from app.admin.group_routes import group_page
from app.admin.pages import Locale
from app.domain.authorization import Permission
from app.domain.calls import BookingOverview, CallStatus, EventKind, not_picked_up
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day
from app.web.call_labels import CALL_STATE_LABELS, EVENT_LABELS, KIND_LABELS, TASK_STATE_LABELS

router = APIRouter(prefix="/admin", include_in_schema=False)

ViewBookings = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BOOKINGS_VIEW, group_param="slug")),
]


@dataclass(frozen=True)
class Chip:
    """A status written out, with a colour tone that is never the only signal."""

    label: str
    tone: str


@dataclass(frozen=True)
class AgendaRow:
    """One booking in the agenda."""

    id: int
    time: str
    kind: str
    first_name: str
    call: Chip
    task: Chip
    host_joined: bool
    participants: int
    not_picked_up: bool
    cancelled: bool


def _integration(request: Request) -> IntegrationService:
    """Return the integration service."""
    service: IntegrationService = request.app.state.integration_service
    return service


def _chips(status: CallStatus, _: BoundGettext) -> tuple[Chip, Chip]:
    """Return the meeting and task state as chips."""
    call_key, call_tone = CALL_STATE_LABELS[status.call]
    task_key, task_tone = TASK_STATE_LABELS[status.task]
    return Chip(_(call_key), call_tone), Chip(_(task_key), task_tone)


def _row(
    item: BookingOverview, group: Group, now: datetime, _: BoundGettext, code: str
) -> AgendaRow:
    """Turn a booking and its status into an agenda row."""
    booking, status, tz = item.booking, item.status, group.tz
    call, task = _chips(status, _)
    limit = timedelta(minutes=group.not_picked_up_minutes)
    return AgendaRow(
        id=booking.id or 0,
        time=f"{format_clock(booking.start, tz, code)}–{format_clock(booking.end, tz, code)}",
        kind=_(KIND_LABELS[booking.kind]),
        first_name=booking.contact.first_name if booking.contact else "–",
        call=call,
        task=task,
        host_joined=status.host_joined,
        participants=status.participants,
        not_picked_up=booking.is_active and not_picked_up(status, booking.start, now, limit),
        cancelled=not booking.is_active,
    )


@router.get("/groups/{slug}/agenda", response_class=HTMLResponse)
async def agenda(
    request: Request, locale: Locale, slug: str, context: ViewBookings, day: str | None = None
) -> HTMLResponse:
    """Show the group's bookings of one day with their call status."""
    _ = locale.gettext
    group = context.group.group
    clock: Clock = request.app.state.clock
    now = clock.now()
    try:
        chosen = date.fromisoformat(day) if day else now.astimezone(group.tz).date()
    except ValueError:
        chosen = now.astimezone(group.tz).date()
    rows = await _integration(request).day_overview(context.principal, slug, chosen)
    return group_page(
        request,
        locale,
        context,
        "admin/group_agenda.html",
        {
            "day_label": format_day(chosen, locale.code),
            "previous_day": (chosen - timedelta(days=1)).isoformat(),
            "next_day": (chosen + timedelta(days=1)).isoformat(),
            "today": now.astimezone(group.tz).date().isoformat(),
            "rows": [_row(item, group, now, _, locale.code) for item in rows],
        },
    )


@dataclass(frozen=True)
class EventView:
    """One timeline entry."""

    time: str
    label: str
    detail: str


@router.get("/groups/{slug}/bookings/{booking_id}", response_class=HTMLResponse)
async def booking_detail(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ViewBookings
) -> HTMLResponse:
    """Show one booking's call status and timeline."""
    _ = locale.gettext
    group = context.group.group
    try:
        timeline = await _integration(request).timeline(context.principal, booking_id)
    except DomainError as error:
        if error.code is ErrorCode.NOT_PERMITTED:
            raise AccessDenied from error
        raise
    if timeline.booking.group_id != group.id:  # an id from another group, via this group's URL
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    booking, status = timeline.booking, timeline.status
    call, task = _chips(status, _)
    tz, code = group.tz, locale.code
    events = [
        EventView(
            f"{format_day(e.at.astimezone(tz).date(), code)}, {format_clock(e.at, tz, code)}",
            _(EVENT_LABELS[e.kind]),
            e.detail,
        )
        for e in timeline.events
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/booking_detail.html",
        {
            "reference": booking.reference,
            "when": f"{format_day(booking.start.astimezone(tz).date(), code)}, "
            f"{format_clock(booking.start, tz, code)}–{format_clock(booking.end, tz, code)}",
            "kind": _(KIND_LABELS[booking.kind]),
            "call": call,
            "task": task,
            "status": status,
            "events": events,
            "end_not_possible": any(e.kind is EventKind.END_NOT_POSSIBLE for e in timeline.events),
            "back": f"/admin/groups/{slug}/agenda?day={booking.start.astimezone(tz).date()}",
        },
    )
