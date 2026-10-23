"""Citizen booking pages: choose a slot, book, view, cancel and reschedule (HANDOVER §5).

Server-rendered and usable without JavaScript. Every response goes through
``app.web.templates.render``; every write is a same-origin, rate-limited POST.

    GET  /g/{slug}?week=           slot picker per week
    GET  /g/{slug}/book?start=     contact form for one slot
    POST /g/{slug}/book            book ─► 303 /b/{token}?status=booked
    GET  /b/{token}                booking (read-only from the change deadline)
    GET  /b/{token}/cancel         confirm ─► POST ─► 303 /b/{token}?status=cancelled
    GET  /b/{token}/reschedule     slot picker ─► POST ─► 303 /b/{new token}?status=rescheduled
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.labels import slot_label
from app.domain.availability import AvailableSlot
from app.domain.booking import BookingStatus
from app.domain.errors import DomainError
from app.domain.ports import Clock
from app.domain.scheduling import Group, PhoneMode
from app.domain.services import BookingDetails, BookingService, ContactInput
from app.i18n.dates import format_clock, format_day, format_day_short, local_day
from app.web.errors import FormError, form_errors, status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.protection import (
    RejectedRequest,
    booking_limit,
    limiter,
    read_limit,
    require_same_origin,
)
from app.web.templates import create_templates, render

router = APIRouter(include_in_schema=False)
templates = create_templates([Path(__file__).parent / "templates"])

HONEYPOT_FIELD = "website"
"""Hidden field that people never fill in; bots that fill every field are refused."""

STATUS_NOTICES: dict[str, str] = {
    "booked": "manage.notice.booked",
    "rescheduled": "manage.notice.rescheduled",
    "cancelled": "manage.notice.cancelled",
}

STATUS_LABELS: dict[BookingStatus, str] = {
    BookingStatus.CONFIRMED: "manage.status.confirmed",
    BookingStatus.CANCELLED: "manage.status.cancelled",
    BookingStatus.COMPLETED: "manage.status.completed",
    BookingStatus.NO_SHOW: "manage.status.no_show",
}

Locale = Annotated[RequestLocale, Depends(resolve_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


def _service(request: Request) -> BookingService:
    """Return the application's booking service."""
    service: BookingService = request.app.state.booking_service
    return service


def _clock(request: Request) -> Clock:
    """Return the application's clock."""
    clock: Clock = request.app.state.clock
    return clock


@dataclass(frozen=True)
class SlotView:
    """One radio button of the slot picker."""

    id: str
    value: str
    label: str


@dataclass(frozen=True)
class DayView:
    """One fieldset of the slot picker."""

    label: str
    slots: list[SlotView]


@dataclass(frozen=True)
class FieldView:
    """One input of the contact form, fully prepared for the template."""

    name: str
    type: str
    autocomplete: str
    label: str
    required: bool
    value: str
    hint: str | None
    error: str | None

    @property
    def described_by(self) -> str:
        """IDs of the hint and error message, for ``aria-describedby``."""
        ids = [f"{self.name}-hint" if self.hint else "", f"{self.name}-error" if self.error else ""]
        return " ".join(i for i in ids if i)


@dataclass(frozen=True)
class WeekView:
    """The week shown by a slot picker, with navigation."""

    monday: date
    heading: str
    previous: str | None
    next: str | None
    days: list[DayView]


def _slot_value(start: datetime) -> str:
    """Return the form value of a slot: its start in UTC, ISO 8601 with ``Z``."""
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_start(value: str | None) -> datetime | None:
    """Parse a slot value; anything but an aware ISO timestamp is ``None``."""
    if not value or len(value) > 32:
        return None
    try:
        start = datetime.fromisoformat(value)
    except ValueError:
        return None
    return start if start.tzinfo is not None else None


def _monday(day: date) -> date:
    """Return the Monday of ``day``'s week."""
    return day - timedelta(days=day.weekday())


async def _week(
    service: BookingService,
    group: Group,
    now: datetime,
    requested: str | None,
    locale: RequestLocale,
    exclude: datetime | None = None,
) -> WeekView:
    """Build the slot picker for a week, clamped to today … horizon.

    Without a requested week the first week that still has free slots is shown, so a visitor
    never lands on an empty week (for example on a Friday evening).
    """
    first = _monday(local_day(now, group.tz))
    last = _monday(local_day(now + timedelta(days=group.horizon_days), group.tz))
    try:
        monday = _monday(date.fromisoformat(requested)) if requested else first
    except ValueError:
        monday = first
    monday = min(max(monday, first), last)
    slots = await service.availability(group.slug, monday, monday + timedelta(days=6))
    while requested is None and not slots and monday < last:
        monday += timedelta(days=7)
        slots = await service.availability(group.slug, monday, monday + timedelta(days=6))
    by_day: dict[date, list[AvailableSlot]] = {}
    for slot in slots:
        if slot.start != exclude:  # e.g. the booking that is being moved
            by_day.setdefault(local_day(slot.start, group.tz), []).append(slot)
    days = [
        DayView(
            label=format_day_short(day, locale.code),
            slots=[
                SlotView(
                    id=f"slot-{slot.start.strftime('%Y%m%d%H%M')}",
                    value=_slot_value(slot.start),
                    label=format_clock(slot.start, group.tz, locale.code),
                )
                for slot in day_slots
            ],
        )
        for day, day_slots in sorted(by_day.items())
    ]
    return WeekView(
        monday=monday,
        heading=locale.gettext("group.week_heading", date=format_day_short(monday, locale.code)),
        previous=(monday - timedelta(days=7)).isoformat() if monday > first else None,
        next=(monday + timedelta(days=7)).isoformat() if monday < last else None,
        days=days,
    )


def _manage_url(token: str, status: str) -> str:
    """Return the management page URL with a one-time notice."""
    return f"/b/{token}?status={status}"


async def _available(
    service: BookingService, group: Group, start: datetime
) -> AvailableSlot | None:
    """Return the slot at ``start`` if it can still be booked."""
    day = local_day(start, group.tz)
    for slot in await service.availability(group.slug, day, day):
        if slot.start == start:
            return slot
    return None


@router.get("/g/{slug}", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def group_page(
    request: Request,
    slug: str,
    locale: Locale,
    week: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    """Show the group's bookable slots for one week."""
    service = _service(request)
    group = await service.get_group(slug)
    view = await _week(service, group, _clock(request).now(), week, locale)
    errors = (
        [FormError(None, locale.gettext("error.slot_unavailable"))]
        if status == "unavailable"
        else []
    )
    context = {"group": group, "week": view, "errors": errors}
    return render(templates, request, "group.html", locale, context)


def _fields(
    group: Group, locale: RequestLocale, errors: list[FormError], values: dict[str, str]
) -> list[FieldView]:
    """Return the contact form inputs: name, email and (per group) phone (HANDOVER B15)."""
    _ = locale.gettext
    by_field = {error.field: error.message for error in errors if error.field}
    fields = [
        FieldView("name", "text", "name", _("form.name"), True, "", None, None),
        FieldView("email", "email", "email", _("form.email"), True, "", _("form.email_hint"), None),
    ]
    if group.phone_mode is not PhoneMode.HIDDEN:
        required = group.phone_mode is PhoneMode.REQUIRED
        fields.append(FieldView("phone", "tel", "tel", _("form.phone"), required, "", None, None))
    return [
        FieldView(
            f.name,
            f.type,
            f.autocomplete,
            f.label,
            f.required,
            values.get(f.name, ""),
            f.hint,
            by_field.get(f.name),
        )
        for f in fields
    ]


def _book_context(
    group: Group,
    start: datetime,
    slot: AvailableSlot | None,
    locale: RequestLocale,
    errors: list[FormError],
    values: dict[str, str],
) -> dict[str, object]:
    """Context of the contact form."""
    return {
        "group": group,
        "start": _slot_value(start),
        "slot_label": slot_label(start, slot.end, group, locale) if slot else "",
        "fields": _fields(group, locale, errors, values),
        "errors": errors,
        "honeypot": HONEYPOT_FIELD,
    }


@router.get("/g/{slug}/book", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def booking_form(
    request: Request, slug: str, locale: Locale, start: str | None = None
) -> Response:
    """Show the contact form for one slot, or send the visitor back to choose one."""
    service = _service(request)
    group = await service.get_group(slug)
    parsed = _parse_start(start)
    slot = await _available(service, group, parsed) if parsed else None
    if parsed is None or slot is None:
        return RedirectResponse(f"/g/{group.slug}?status=unavailable", status_code=303)
    return render(
        templates, request, "book.html", locale, _book_context(group, parsed, slot, locale, [], {})
    )


@router.post("/g/{slug}/book", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def book(request: Request, slug: str, locale: Locale, _: SameOrigin) -> Response:
    """Book the slot; on success redirect to the management page."""
    service = _service(request)
    group = await service.get_group(slug)
    form = await request.form()
    if form.get(HONEYPOT_FIELD):
        raise RejectedRequest(400)
    start = _parse_start(str(form.get("start", "")))
    if start is None:
        raise RejectedRequest(400)
    values = {key: str(form.get(key, ""))[:300] for key in ("name", "email", "phone")}
    try:
        receipt = await service.book(
            group_slug=group.slug,
            start=start,
            contact=ContactInput(values["name"], values["email"], values["phone"] or None),
            locale=locale.code,
        )
    except DomainError as error:
        slot = await _available(service, group, start)
        context = _book_context(
            group, start, slot, locale, form_errors(error, locale.gettext), values
        )
        response = render(templates, request, "book.html", locale, context)
        response.status_code = status_for(error)
        return response
    return RedirectResponse(_manage_url(receipt.management_token, "booked"), status_code=303)


def _manage_context(
    details: BookingDetails, locale: RequestLocale, status: str | None
) -> dict[str, object]:
    """Context of the management page. Shows no contact details, only the appointment."""
    booking, group = details.booking, details.group
    deadline = details.change_deadline
    return {
        "group": group,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
        "status_label": locale.gettext(STATUS_LABELS[booking.status]),
        "active": booking.is_active,
        "changeable": details.changeable,
        "deadline": (
            f"{format_day(local_day(deadline, group.tz), locale.code)}, "
            f"{format_clock(deadline, group.tz, locale.code)}"
        ),
        "notice": locale.gettext(STATUS_NOTICES[status]) if status in STATUS_NOTICES else None,
    }


@router.get("/b/{token}", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def manage_page(
    request: Request, token: str, locale: Locale, status: str | None = None
) -> HTMLResponse:
    """Show a booking; read-only once its change deadline has passed."""
    details = await _service(request).find(token)
    context = {**_manage_context(details, locale, status), "token": token}
    return render(templates, request, "manage.html", locale, context)


@router.get("/b/{token}/cancel", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def cancel_page(request: Request, token: str, locale: Locale) -> Response:
    """Ask for confirmation before cancelling."""
    details = await _service(request).find(token)
    if not details.changeable:
        return RedirectResponse(f"/b/{token}", status_code=303)
    context = {**_manage_context(details, locale, None), "token": token}
    return render(templates, request, "cancel.html", locale, context)


@router.post("/b/{token}/cancel", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def cancel(request: Request, token: str, locale: Locale, _: SameOrigin) -> Response:
    """Cancel the booking."""
    await _service(request).cancel(token)
    return RedirectResponse(_manage_url(token, "cancelled"), status_code=303)


async def _reschedule_response(
    request: Request,
    token: str,
    locale: RequestLocale,
    week: str | None,
    errors: list[FormError],
    status_code: int = 200,
) -> Response:
    """Render the slot picker for moving a booking, or go back if it cannot change."""
    service = _service(request)
    details = await service.find(token)
    if not details.changeable:
        return RedirectResponse(f"/b/{token}", status_code=303)
    view = await _week(
        service, details.group, _clock(request).now(), week, locale, details.booking.start
    )
    context = {
        **_manage_context(details, locale, None),
        "token": token,
        "week": view,
        "errors": errors,
    }
    response = render(templates, request, "reschedule.html", locale, context)
    response.status_code = status_code
    return response


@router.get("/b/{token}/reschedule", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def reschedule_page(
    request: Request, token: str, locale: Locale, week: str | None = None
) -> Response:
    """Show the slot picker for a new time."""
    return await _reschedule_response(request, token, locale, week, [])


@router.post("/b/{token}/reschedule", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def reschedule(request: Request, token: str, locale: Locale, _: SameOrigin) -> Response:
    """Move the booking; the new booking gets a new management link."""
    form = await request.form()
    start = _parse_start(str(form.get("start", "")))
    if start is None:
        raise RejectedRequest(400)
    try:
        receipt = await _service(request).reschedule(token, start)
    except DomainError as error:
        errors = form_errors(error, locale.gettext)
        week = _monday(start.date()).isoformat()
        return await _reschedule_response(request, token, locale, week, errors, status_for(error))
    return RedirectResponse(_manage_url(receipt.management_token, "rescheduled"), status_code=303)
