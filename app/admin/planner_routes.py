"""A planner's changes from the group agenda (ADR 0044): move, cancel, accept a shortfall.

Every route needs ``bookings.manage`` on the group with a fresh login (ADR 0015); the domain
decides again (``PlannerService``). Each form names the booking by reference and first name
only, as the agenda does. The citizen is always told; the reason is chosen from a fixed list,
never typed. After a change the planner lands back on the day in the agenda.

    GET  /admin/groups/{slug}/agenda/bookings/{booking_id}/move     bookings.manage
    POST /admin/groups/{slug}/agenda/bookings/{booking_id}/move     bookings.manage
    GET  /admin/groups/{slug}/agenda/bookings/{booking_id}/cancel   bookings.manage
    POST /admin/groups/{slug}/agenda/bookings/{booking_id}/cancel   bookings.manage
    POST /admin/groups/{slug}/agenda/accept                         bookings.manage
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import (
    Field,
    FormData,
    Option,
    raise_refusal,
    read_form,
    redirect_done,
    slot_options,
)
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin
from app.domain.authorization import Permission
from app.domain.booking import Booking, ChangeReason
from app.domain.errors import DomainError
from app.domain.planner_service import PlannerService
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day, local_day
from app.web.errors import status_for
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.slot_values import parse_start

router = APIRouter(prefix="/admin", include_in_schema=False)

MOVE_DAYS = 28
"""How many days ahead the move form offers free slots (as the closure's move form)."""

REASON_LABELS: dict[ChangeReason, str] = {
    ChangeReason.STAFFING: "admin.planner.reason_staffing",
    ChangeReason.TECHNICAL: "admin.planner.reason_technical",
    ChangeReason.UNFORESEEN: "admin.planner.reason_unforeseen",
}
"""What the planner sees per reason (the citizen gets ``message.reason.<code>``)."""

ManageBookings = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BOOKINGS_MANAGE, group_param="slug", fresh=True)),
]


@dataclass(frozen=True)
class BookingView:
    """The booking a form is about: reference, first name and time."""

    id: int
    reference: str
    first_name: str
    when: str


def _planner(request: Request) -> PlannerService:
    """Return the application's planner service."""
    service: PlannerService = request.app.state.planner_service
    return service


def _booking_view(booking: Booking, group: Group, code: str) -> BookingView:
    """Return what a form shows of ``booking``."""
    tz = group.tz
    return BookingView(
        booking.id or 0,
        booking.reference,
        booking.contact.first_name if booking.contact else "–",
        f"{format_day(local_day(booking.start, tz), code)}, "
        f"{format_clock(booking.start, tz, code)}",
    )


def _agenda(slug: str, day: date) -> str:
    """Return the agenda URL of ``day``."""
    return f"/admin/groups/{slug}/agenda?day={day.isoformat()}"


async def _changeable(request: Request, context: AdminContext, booking_id: int) -> Booking:
    """Return the booking if it may still be changed; refusals become the HTTP error."""
    try:
        return await _planner(request).booking(
            context.principal, context.group.group.slug, booking_id
        )
    except DomainError as error:
        raise_refusal(error)
        raise


# --- move ---------------------------------------------------------------------------------


async def _move_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    booking: Booking,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form that moves one booking to a free slot."""
    _ = locale.gettext
    group = context.group.group
    clock: Clock = request.app.state.clock
    today = local_day(clock.now(), group.tz)
    bookings: BookingService = request.app.state.booking_service
    free = await bookings.availability(group.slug, today, today + timedelta(days=MOVE_DAYS))
    options = slot_options(
        [s.start for s in free if s.start != booking.start], group.tz, locale.code
    )
    field = Field(
        "start",
        _("admin.planner.move_start"),
        "select",
        form.text("start"),
        hint=_("admin.planner.move_hint", days=MOVE_DAYS),
        error=form.error_for("start"),
        options=options,
    )
    return group_page(
        request,
        locale,
        context,
        "admin/planner_move.html",
        {
            "booking": _booking_view(booking, group, locale.code),
            "field": field,
            "has_slots": bool(options),
            "back": _agenda(group.slug, local_day(booking.start, group.tz)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/agenda/bookings/{booking_id}/move", response_class=HTMLResponse)
async def move_form(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ManageBookings
) -> HTMLResponse:
    """Show the free slots a booking can move to."""
    booking = await _changeable(request, context, booking_id)
    return await _move_response(request, locale, context, booking, FormData({}, locale.gettext))


@router.post("/groups/{slug}/agenda/bookings/{booking_id}/move", response_class=HTMLResponse)
async def move(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Move a booking; the citizen gets the change message with the new time."""
    form = await read_form(request, locale)
    start = parse_start(form.text("start"))
    if start is None:
        raise RejectedRequest(400)
    booking = await _changeable(request, context, booking_id)
    accept = form.text("accept_shortfall") == "yes"
    try:
        moved = await _planner(request).move(
            context.principal, slug, booking_id, start, accept_shortfall=accept
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _move_response(request, locale, context, booking, form, status_for(error))
    group = context.group.group
    return redirect_done(_agenda(slug, local_day(moved.start, group.tz)), "moved", 1)


# --- cancel -------------------------------------------------------------------------------


def _reason_field(form: FormData, _: BoundGettext) -> Field:
    """Return the select of the fixed reasons."""
    return Field(
        "reason",
        _("admin.planner.reason"),
        "select",
        form.text("reason") or ChangeReason.STAFFING.value,
        hint=_("admin.planner.reason_hint"),
        error=form.error_for("reason"),
        options=tuple(Option(r.value, _(label)) for r, label in REASON_LABELS.items()),
    )


def _cancel_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    booking: Booking,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form that cancels one booking with a reason."""
    group = context.group.group
    return group_page(
        request,
        locale,
        context,
        "admin/planner_cancel.html",
        {
            "booking": _booking_view(booking, group, locale.code),
            "field": _reason_field(form, locale.gettext),
            "back": _agenda(group.slug, local_day(booking.start, group.tz)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/agenda/bookings/{booking_id}/cancel", response_class=HTMLResponse)
async def cancel_form(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ManageBookings
) -> HTMLResponse:
    """Ask for the reason before cancelling a booking."""
    booking = await _changeable(request, context, booking_id)
    return _cancel_response(request, locale, context, booking, FormData({}, locale.gettext))


@router.post("/groups/{slug}/agenda/bookings/{booking_id}/cancel", response_class=HTMLResponse)
async def cancel(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Cancel a booking; the citizen gets the cancellation message with the reason."""
    form = await read_form(request, locale)
    choice = form.choice("reason", {r.value for r in ChangeReason})
    booking = await _changeable(request, context, booking_id)
    if choice is None:
        return _cancel_response(request, locale, context, booking, form, 422)
    try:
        await _planner(request).cancel(context.principal, slug, booking_id, ChangeReason(choice))
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return _cancel_response(request, locale, context, booking, form, status_for(error))
    group = context.group.group
    return redirect_done(_agenda(slug, local_day(booking.start, group.tz)), "cancelled", 1)


# --- accept a shortfall -------------------------------------------------------------------


@router.post("/groups/{slug}/agenda/accept", response_class=HTMLResponse)
async def accept(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageBookings
) -> Response:
    """Accept one slot's shortfall as it is ("leave as is"), until its counts change."""
    form = await read_form(request, locale)
    start = parse_start(form.text("start"))
    if start is None:
        raise RejectedRequest(400)
    try:
        await _planner(request).accept(context.principal, slug, start)
    except DomainError as error:
        raise_refusal(error)
        raise
    group = context.group.group
    anchor = f"slot-{start:%Y%m%dT%H%M}"
    return redirect_done(
        _agenda(slug, local_day(start, group.tz)), "shortfall_accepted", anchor=anchor
    )
