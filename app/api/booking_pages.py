"""Citizen booking pages: choose a slot, book, view, cancel and reschedule (HANDOVER §5).

Server-rendered and usable without JavaScript. Every response goes through
``app.web.templates.render``; every write is a same-origin, rate-limited POST.

    GET  /g/{slug}?month=&day=     slot picker: month calendar + times of one day (ADR 0020);
                                   the closure page instead while the team is closed (ADR 0037)
    GET  /g/{slug}/closure         closure page of an active or upcoming closure, else ─► 303
    GET  /g/{slug}/closure/image   that closure's image (ADR 0037 §3), else 404
    GET  /g/{slug}/book?start=     contact form for one slot
    POST /g/{slug}/book            book ─► 303 /b/{token}?status=booked
    GET  /b/{token}                booking (read-only from the change deadline; a booking on
                                   hold by an emergency closure shows its notice, ADR 0024)
    GET  /b/{token}/cancel         confirm ─► POST ─► 303 /b/{token}?status=cancelled
    GET  /b/{token}/reschedule     slot picker ─► ?start= confirm ─► POST
                                   ─► 303 /b/{new token}?status=rescheduled
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.citizen import citizen_page
from app.api.contact_form import (
    HONEYPOT_FIELD,
    ContactForm,
    contact_input,
    load_contact_form,
    read_contact,
)
from app.api.labels import slot_label
from app.api.slot_picker import parse_filter, picker, slot_value
from app.api.trace import bind_group_from_path
from app.domain.availability import AvailableSlot
from app.domain.booking import BookingStatus
from app.domain.errors import DomainError
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.domain.services import BookingDetails, BookingService
from app.i18n.dates import format_clock, format_day, local_day
from app.web.closure_labels import closure_view
from app.web.errors import FormError, form_errors, status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import follow, follow_group_from_path
from app.web.protection import (
    RejectedRequest,
    asset_limit,
    booking_limit,
    limiter,
    read_limit,
    require_same_origin,
)
from app.web.slot_values import parse_start

router = APIRouter(
    include_in_schema=False,
    dependencies=[Depends(bind_group_from_path), Depends(follow_group_from_path)],
)

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
    BookingStatus.SUSPENDED: "manage.status.suspended",
    BookingStatus.AWAITING_REBOOK: "manage.status.awaiting_rebook",
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
    month: str | None = None,
    day: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    """Show the group's month calendar and the free times of one day.

    While the team is closed, the closure page takes the calendar's place (ADR 0037 §5).
    """
    service = _service(request)
    group = await service.get_group(slug)
    now = _clock(request).now()
    closure = await service.closure_notice(group)
    banner = closure_view(closure, group, locale, now) if closure else None
    if banner and banner.active:
        return await citizen_page(request, locale, "closure.html", group, {"closure": banner})
    view = await picker(
        service,
        group,
        _clock(request).now(),
        locale,
        f"/g/{group.slug}",
        month=month,
        day=day,
        slot_filter=parse_filter(request.query_params.multi_items()),
    )
    errors = (
        [FormError(None, locale.gettext("error.slot_unavailable"))]
        if status == "unavailable"
        else []
    )
    context = {
        "picker": view,
        "errors": errors,
        "direct_call": await service.direct_call_availability(slug),
        "closure": banner,
        "closure_page": f"/g/{group.slug}/closure",
    }
    return await citizen_page(request, locale, "group.html", group, context)


@router.get("/g/{slug}/closure", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def closure_page(request: Request, slug: str, locale: Locale) -> Response:
    """Show the closure page of the group; without a closure to show, go to its calendar."""
    service = _service(request)
    group = await service.get_group(slug)
    closure = await service.closure_notice(group)
    if closure is None:
        return RedirectResponse(f"/g/{group.slug}", status_code=303)
    banner = closure_view(closure, group, locale, _clock(request).now())
    return await citizen_page(request, locale, "closure.html", group, {"closure": banner})


@router.get("/g/{slug}/closure/image")
@limiter.limit(asset_limit)
async def closure_image(request: Request, slug: str) -> Response:
    """Serve the image of the group's visible closure, or 404 (ADR 0037 §3).

    Only a visible closure's image is public. The type comes from the magic bytes checked on
    upload; the default headers add ``nosniff`` and ``default-src 'none'``. ``no-cache``
    makes browsers ask again, so an image stops being shown once its closure has ended.
    """
    service = _service(request)
    found = await service.closure_image(await service.get_group(slug))
    if found is None:
        return Response(status_code=404)
    return Response(found.data, media_type=found.media_type, headers={"cache-control": "no-cache"})


def _book_context(
    contact: ContactForm,
    start: datetime,
    slot: AvailableSlot | None,
    locale: RequestLocale,
    errors: list[FormError],
    values: dict[str, str],
) -> dict[str, object]:
    """Context of the contact form."""
    group = contact.group
    return {
        "group": group,
        "start": slot_value(start),
        "slot_label": slot_label(start, slot.end, group, locale) if slot else "",
        "fields": contact.fields(locale, errors, values),
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
    parsed = parse_start(start)
    slot = await _available(service, group, parsed) if parsed else None
    if parsed is None or slot is None:
        return RedirectResponse(f"/g/{group.slug}?status=unavailable", status_code=303)
    contact = await load_contact_form(service, group, _clock(request).now())
    context = _book_context(contact, parsed, slot, locale, [], {})
    return await citizen_page(request, locale, "book.html", group, context)


@router.post("/g/{slug}/book", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def book(request: Request, slug: str, locale: Locale, _: SameOrigin) -> Response:
    """Book the slot; on success redirect to the management page."""
    service = _service(request)
    group = await service.get_group(slug)
    form = await request.form()
    values = read_contact(form)
    start = parse_start(str(form.get("start", "")))
    if start is None:
        raise RejectedRequest(400)
    try:
        receipt = await service.book(
            group_slug=group.slug,
            start=start,
            contact=contact_input(values),
            locale=locale.code,
        )
    except DomainError as error:
        slot = await _available(service, group, start)
        contact = await load_contact_form(service, group, _clock(request).now())
        context = _book_context(contact, start, slot, locale, contact.errors(error, locale), values)
        return await citizen_page(request, locale, "book.html", group, context, status_for(error))
    return RedirectResponse(_manage_url(receipt.management_token, "booked"), status_code=303)


def _manage_context(
    details: BookingDetails, locale: RequestLocale, status: str | None, now: datetime
) -> dict[str, object]:
    """Context of the management page. Shows no contact details, only the appointment."""
    booking, group = details.booking, details.group
    deadline = details.change_deadline
    closure = details.closure
    return {
        "suspended": booking.is_suspended,
        # ADR 0044: a planner asked for a new time; the request lapses at ``rebook_by``.
        "awaiting_rebook": booking.awaits_rebook,
        "rebook_by": (
            f"{format_day(local_day(booking.rebook_by, group.tz), locale.code)}, "
            f"{format_clock(booking.rebook_by, group.tz, locale.code)}"
            if booking.rebook_by
            else None
        ),
        "closure": closure_view(closure, group, locale, now) if closure else None,
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
    follow(request, details.group.organisation_id)
    context = {**_manage_context(details, locale, status, _clock(request).now()), "token": token}
    return await citizen_page(request, locale, "manage.html", details.group, context)


@router.get("/b/{token}/cancel", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def cancel_page(request: Request, token: str, locale: Locale) -> Response:
    """Ask for confirmation before cancelling."""
    details = await _service(request).find(token)
    follow(request, details.group.organisation_id)
    if not details.changeable:
        return RedirectResponse(f"/b/{token}", status_code=303)
    context = {**_manage_context(details, locale, None, _clock(request).now()), "token": token}
    return await citizen_page(request, locale, "cancel.html", details.group, context)


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
    *,
    month: str | None = None,
    day: str | None = None,
    start: str | None = None,
    errors: list[FormError] | None = None,
    status_code: int = 200,
) -> Response:
    """Render the picker for moving a booking, or the confirmation of a chosen time.

    Goes back to the booking page when it can no longer change.
    """
    service = _service(request)
    details = await service.find(token)
    follow(request, details.group.organisation_id)
    group = details.group
    if not details.changeable:
        return RedirectResponse(f"/b/{token}", status_code=303)
    context: dict[str, object] = {
        **_manage_context(details, locale, None, _clock(request).now()),
        "token": token,
        "errors": errors or [],
    }
    chosen = parse_start(start)
    slot = await _available(service, group, chosen) if chosen else None
    if chosen is not None and slot is not None and chosen != details.booking.start:
        context["new_start"] = slot_value(chosen)
        context["new_label"] = slot_label(chosen, slot.end, group, locale)
        name = "reschedule_confirm.html"
    else:
        context["picker"] = await picker(
            service,
            group,
            _clock(request).now(),
            locale,
            f"/b/{token}/reschedule",
            month=month,
            day=day,
            exclude=details.booking.start,
            slot_filter=parse_filter(request.query_params.multi_items()),
        )
        name = "reschedule.html"
    return await citizen_page(request, locale, name, group, context, status_code)


@router.get("/b/{token}/reschedule", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def reschedule_page(
    request: Request,
    token: str,
    locale: Locale,
    month: str | None = None,
    day: str | None = None,
    start: str | None = None,
) -> Response:
    """Show the picker for a new time, or confirm the chosen one (``?start=``)."""
    return await _reschedule_response(request, token, locale, month=month, day=day, start=start)


@router.post("/b/{token}/reschedule", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def reschedule(request: Request, token: str, locale: Locale, _: SameOrigin) -> Response:
    """Move the booking; the new booking gets a new management link."""
    form = await request.form()
    start = parse_start(str(form.get("start", "")))
    if start is None:
        raise RejectedRequest(400)
    try:
        receipt = await _service(request).reschedule(token, start)
    except DomainError as error:
        return await _reschedule_response(
            request,
            token,
            locale,
            day=start.date().isoformat(),
            errors=form_errors(error, locale.gettext),
            status_code=status_for(error),
        )
    return RedirectResponse(_manage_url(receipt.management_token, "rescheduled"), status_code=303)
