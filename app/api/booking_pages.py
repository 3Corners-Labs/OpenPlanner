"""Participant booking pages: choose a slot, book, view, cancel and reschedule (HANDOVER §5).

Server-rendered and usable without JavaScript. Every response goes through
``app.web.templates.render``; every write is a same-origin, rate-limited POST.

    GET  /g/{slug}?month=&day=     slot picker: month calendar + times of one day (ADR 0020);
                                   the closure page instead while the team is closed (ADR 0037)
    GET  /g/{slug}/closure         closure page of an active or upcoming closure, else ─► 303
    GET  /g/{slug}/closure/image   that closure's image (ADR 0037 §3), else 404
    GET  /g/{slug}/book?start=     contact form for one slot
    POST /g/{slug}/book            book ─► 303 /g/{slug}/booked?start= (ADR 0056: the token
                                   goes to the participant by email only, never to the browser)
    GET  /g/{slug}/booked?start=   "check your email to confirm"
    GET  /b/{token}[/page]         the link of an email (ADR 0011): token checked, cookie set,
                                   303 to the same page under /b/m/{ref} (ADR 0093)
    GET  /b/m/{ref}/confirm        the appointment and one button; a GET never confirms, mail
                                   scanners open links in advance (ADR 0056 §8)
    POST /b/m/{ref}/confirm        confirm ─► 303 /b/m/{ref}?status=confirmed (or lapsed)
    GET  /b/m/{ref}                booking (no move from the change lead, no cancel from the
                                   start, ADR 0069; a booking on hold by an emergency
                                   closure shows its notice, ADR 0024)
    GET  /b/m/{ref}/cancel         confirm ─► POST ─► 303 /b/m/{ref}?status=cancelled
    GET  /b/m/{ref}/reschedule     slot picker ─► ?start= confirm ─► POST
                                   ─► 303 /b/m/{new ref}?status=rescheduled
    GET  /b/m/{ref}/details        correct name, phone and answers (ADR 0056 point 10) ─► POST
                                   ─► 303 /b/m/{ref}?status=details
                                   (awaiting a new time: up to three suggested times, ADR 0057)
    GET  /b/m/{ref}/proposal       the proposed new time, one button ─► POST
                                   ─► 303 /b/m/{new ref}?status=rescheduled (ADR 0057 point 8)
    GET  /b/m/{ref}/change         a bot's change request (ADR 0023 step 2), one button ─► POST
                                   ─► 303 ?status=cancelled, or /b/m/{new ref}?status=rescheduled
The cookie rule is in ``app.api.link_session``; the join, trial and waiting pages are in
``join_pages``, ``trial_pages`` and ``direct_pages``.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.contact_form import (
    HONEYPOT_FIELD,
    SUBJECT_INPUT,
    ContactForm,
    contact_input,
    contact_values,
    load_contact_form,
    read_contact,
)
from app.api.flow_choice import checked_flow
from app.api.labels import slot_label
from app.api.link_session import authorise, exchange, start
from app.api.participant import participant_page
from app.api.slot_picker import parse_filter, picker, slot_value
from app.api.trace import bind_group_from_path
from app.domain.availability import AvailableSlot
from app.domain.booking import Booking, BookingStatus
from app.domain.change_request_service import ChangeRequestService
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.domain.service_flows import IntakeMode
from app.domain.services import BookingDetails, BookingService
from app.i18n.dates import format_clock, format_day, local_day
from app.web.closure_labels import closure_view, delete_by_label
from app.web.errors import FormError, form_errors, status_for
from app.web.locale import RequestLocale, request_locale
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
    "rescheduled": "manage.notice.rescheduled",
    "cancelled": "manage.notice.cancelled",
    "confirmed": "manage.notice.confirmed",
    "lapsed": "manage.notice.lapsed",
    "details": "manage.notice.details",
    "proposal_gone": "manage.notice.proposal_gone",
    "change_gone": "manage.notice.change_gone",
}

STATUS_LABELS: dict[BookingStatus, str] = {
    BookingStatus.CONFIRMED: "manage.status.confirmed",
    BookingStatus.CANCELLED: "manage.status.cancelled",
    BookingStatus.COMPLETED: "manage.status.completed",
    BookingStatus.NO_SHOW: "manage.status.no_show",
    BookingStatus.SUSPENDED: "manage.status.suspended",
    BookingStatus.AWAITING_REBOOK: "manage.status.awaiting_rebook",
    BookingStatus.PENDING_CONFIRMATION: "manage.status.pending_confirmation",
}

Locale = Annotated[RequestLocale, Depends(request_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


def _service(request: Request) -> BookingService:
    """Return the application's booking service."""
    service: BookingService = request.app.state.booking_service
    return service


def _clock(request: Request) -> Clock:
    """Return the application's clock."""
    clock: Clock = request.app.state.clock
    return clock


def _manage_url(public_ref: str, status: str) -> str:
    """Return the management page URL with a one-time notice."""
    return f"/b/m/{public_ref}?status={status}"


async def _available(
    service: BookingService,
    group: Group,
    start: datetime,
    subject: str | None = None,
    *,
    flow: str | None = None,
    booking: Booking | None = None,
) -> AvailableSlot | None:
    """Return the slot at ``start`` if it can still be booked (for ``subject``, ADR 0081).

    ``flow`` is the checked flow code of the page; ``booking`` a booking being moved, judged on
    the times of its own slot (``move_times``, ADR 0122), with its own subject.
    """
    day = local_day(start, group.tz)
    slots = (
        await service.move_times(booking, day, day)
        if booking is not None
        else await service.availability(group.slug, day, day, subject, flow=flow)
    )
    for slot in slots:
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
    flow: str | None = None,
) -> HTMLResponse:
    """Show the group's month calendar and the free times of one day.

    While the team is closed, the closure page takes the calendar's place (ADR 0037 §5). An
    optional ``flow`` (ADR 0121 step 3a) must resolve for the appointment path, else the page
    is refused; it is carried to the booking form.
    """
    service = _service(request)
    group = await service.get_group(slug)
    now = _clock(request).now()
    closure = await service.closure_notice(group)
    banner = closure_view(closure, group, locale, now) if closure else None
    if closure and banner and banner.active:
        deleted_by = await service.closure_delete_by(group, closure)
        banner = closure_view(closure, group, locale, now, deleted_by)
        return await participant_page(request, locale, "closure.html", group, {"closure": banner})
    chosen_flow = await checked_flow(service, slug, IntakeMode.APPOINTMENT, flow)
    slot_filter = replace(
        parse_filter(request.query_params.multi_items(), group.routing), flow=chosen_flow
    )
    view = await picker(
        service,
        group,
        _clock(request).now(),
        locale,
        f"/g/{group.slug}",
        month=month,
        day=day,
        slot_filter=slot_filter,
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
        # ADR 0081 point 4: the subject first, so the calendar shows only the blocks that
        # offer it; the booking form asks it again, pre-selected.
        "subjects": [
            {
                "value": s.code,
                "label": s.label(locale.code),
                "selected": s.code == slot_filter.subject,
            }
            for s in group.routing.subjects
        ],
    }
    return await participant_page(request, locale, "group.html", group, context)


@router.get("/g/{slug}/closure", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def closure_page(request: Request, slug: str, locale: Locale) -> Response:
    """Show the closure page of the group; without a closure to show, go to its calendar."""
    service = _service(request)
    group = await service.get_group(slug)
    closure = await service.closure_notice(group)
    if closure is None:
        return RedirectResponse(f"/g/{group.slug}", status_code=303)
    banner = closure_view(
        closure,
        group,
        locale,
        _clock(request).now(),
        await service.closure_delete_by(group, closure),
    )
    return await participant_page(request, locale, "closure.html", group, {"closure": banner})


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
    flow: str | None = None,
) -> dict[str, object]:
    """Context of the contact form; ``flow`` is the checked flow code the page was opened with."""
    group = contact.group
    return {
        "group": group,
        "start": slot_value(start),
        "slot_label": slot_label(start, slot.end, group, locale) if slot else "",
        "fields": contact.fields(locale, errors, values),
        "errors": errors,
        "honeypot": HONEYPOT_FIELD,
        "flow": flow,
    }


@router.get("/g/{slug}/book", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def booking_form(
    request: Request,
    slug: str,
    locale: Locale,
    start: str | None = None,
    subject: str | None = None,
    flow: str | None = None,
) -> Response:
    """Show the contact form for one slot, or send the visitor back to choose one.

    A ``subject`` chosen before the calendar (ADR 0081) pre-selects the form's first question.
    An optional ``flow`` (ADR 0121 step 3a) must resolve for the appointment path; the form
    posts it back.
    """
    service = _service(request)
    group = await service.get_group(slug)
    chosen_flow = await checked_flow(service, slug, IntakeMode.APPOINTMENT, flow)
    parsed = parse_start(start)
    chosen = group.routing.subject(subject)
    code = chosen.code if chosen else None
    slot = await _available(service, group, parsed, code, flow=chosen_flow) if parsed else None
    if parsed is None or slot is None:
        again = [("status", "unavailable")] + ([("flow", chosen_flow)] if chosen_flow else [])
        return RedirectResponse(f"/g/{group.slug}?{urlencode(again)}", status_code=303)
    contact = await load_contact_form(service, group, _clock(request).now())
    values = {SUBJECT_INPUT: chosen.code} if chosen else {}
    context = _book_context(contact, parsed, slot, locale, [], values, chosen_flow)
    return await participant_page(request, locale, "book.html", group, context)


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
    chosen_flow = await checked_flow(service, slug, IntakeMode.APPOINTMENT, form.get("flow"))
    try:
        receipt = await service.book(
            group_slug=group.slug,
            start=start,
            contact=contact_input(values),
            locale=locale.code,
            flow=chosen_flow,
        )
    except DomainError as error:
        slot = await _available(service, group, start, flow=chosen_flow)
        contact = await load_contact_form(service, group, _clock(request).now())
        context = _book_context(
            contact, start, slot, locale, contact.errors(error, locale), values, chosen_flow
        )
        return await participant_page(
            request, locale, "book.html", group, context, status_for(error)
        )
    booked = f"/g/{group.slug}/booked?start={slot_value(receipt.booking.start)}"
    return RedirectResponse(booked, status_code=303)


@router.get("/g/{slug}/booked", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def booked_page(
    request: Request, slug: str, locale: Locale, start: str | None = None
) -> Response:
    """Ask the participant to confirm by the link in their email (ADR 0056).

    The page knows no booking: ``start`` only repeats the chosen time, and is left out when
    it does not parse.
    """
    group = await _service(request).get_group(slug)
    parsed = parse_start(start)
    length = timedelta(minutes=group.slot_minutes)
    context: dict[str, object] = {
        "group": group,
        "slot_label": slot_label(parsed, parsed + length, group, locale) if parsed else None,
    }
    return await participant_page(request, locale, "booked.html", group, context)


def _moment(moment: datetime | None, group: Group, locale: RequestLocale) -> str | None:
    """Return ``moment`` as local date and time for the page, or ``None``."""
    if moment is None:
        return None
    return (
        f"{format_day(local_day(moment, group.tz), locale.code)}, "
        f"{format_clock(moment, group.tz, locale.code)}"
    )


def _manage_context(
    details: BookingDetails, locale: RequestLocale, status: str | None, now: datetime
) -> dict[str, object]:
    """Context of the management page. Shows no contact details, only the appointment."""
    booking, group = details.booking, details.group
    deadline = details.change_deadline
    closure = details.closure
    return {
        # ADR 0056: not confirmed yet; it lapses at ``confirm_by``.
        "pending": booking.awaits_confirmation,
        "confirm_by": _moment(booking.confirm_by, group, locale),
        "lapsed": booking.lapsed_unconfirmed,
        "cancellable": details.cancellable,
        "suspended": booking.is_suspended,
        # ADR 0044: a planner asked for a new time; the request lapses at ``rebook_by``.
        "awaiting_rebook": booking.awaits_rebook,
        "rebook_by": _moment(booking.rebook_by, group, locale),
        # ADR 0057: a proposed new time held for the participant until ``confirm_by``.
        "proposal_label": slot_label(details.proposal.start, details.proposal.end, group, locale)
        if details.proposal
        else None,
        "proposal_by": _moment(details.proposal.confirm_by, group, locale)
        if details.proposal
        else None,
        "closure": closure_view(closure, group, locale, now) if closure else None,
        "delete_by": delete_by_label(details.delete_by, group, locale),
        "group": group,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
        "status_label": locale.gettext(STATUS_LABELS[booking.status]),
        "active": booking.is_active,
        "changeable": details.changeable,
        "correctable": details.correctable,
        "trial_offered": group.trial_call_enabled and group.video_enabled,
        "deadline": _moment(deadline, group, locale),
        "notice": locale.gettext(STATUS_NOTICES[status]) if status in STATUS_NOTICES else None,
        # ADR 0060 §7: the footer mark settles once when the booking has just been confirmed.
        "footer_motion": "settle" if status == "confirmed" else "",
    }


LEGACY_SUBPATHS = (
    "",
    "/confirm",
    "/cancel",
    "/reschedule",
    "/details",
    "/proposal",
    "/change",
    "/wait",
    "/wait/status",
    "/wait/leave",
    "/join",
    "/join/go",
    "/test",
    "/test/go",
)
"""Pages an email or a bookmark made before ADR 0093 may still link to under ``/b/{token}``."""


@limiter.limit(read_limit)
async def exchange_legacy_link(request: Request, token: str) -> Response:
    """Open a link from the email: check the token, set the cookie, go to the same page.

    The page is named by the route that matched (``/b/{token}/wait`` ─► ``/b/m/{ref}/wait``), and
    only the language is carried along. A POST (a form left open from before) is answered the
    same way: it is not carried out, the browser gets the page whose button it would have
    pressed.
    """
    path = str(request.scope["route"].path)
    return await exchange(request, token, path.removeprefix("/b/{token}"))


for _suffix in LEGACY_SUBPATHS:
    router.add_api_route(
        f"/b/{{token}}{_suffix}",
        exchange_legacy_link,
        methods=["GET", "POST"] if _suffix else ["GET"],
        response_class=HTMLResponse,
    )


async def _details(request: Request, public_ref: str) -> BookingDetails:
    """Authorise the cookie for ``public_ref`` and return the booking with its group."""
    ref = await authorise(request, public_ref)
    details = await _service(request).find(ref)
    follow(request, details.group.organisation_id)
    return details


def _page_context(
    details: BookingDetails,
    locale: RequestLocale,
    public_ref: str,
    status: str | None,
    now: datetime,
) -> dict[str, object]:
    """Context of a page under ``/b/m/{public_ref}``: the booking and its reference."""
    return {**_manage_context(details, locale, status, now), "ref": public_ref}


@router.get("/b/m/{public_ref}", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def manage_page(
    request: Request, public_ref: str, locale: Locale, status: str | None = None
) -> HTMLResponse:
    """Show a booking: movable until its change deadline, cancellable until its start (ADR 0069)."""
    details = await _details(request, public_ref)
    context = _page_context(details, locale, public_ref, status, _clock(request).now())
    context["appointment_code"] = _appointment_code(request, details)
    return await participant_page(request, locale, "manage.html", details.group, context)


def _appointment_code(request: Request, details: BookingDetails) -> str | None:
    """Return the code a bot asks for, where the organisation takes change requests (ADR 0023)."""
    return _change_service(request).code(details.booking, details.group.organisation_id)


@router.get("/b/m/{public_ref}/cancel", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def cancel_page(request: Request, public_ref: str, locale: Locale) -> Response:
    """Ask for confirmation before cancelling."""
    details = await _details(request, public_ref)
    if not details.cancellable:
        return RedirectResponse(f"/b/m/{public_ref}", status_code=303)
    context = _page_context(details, locale, public_ref, None, _clock(request).now())
    return await participant_page(request, locale, "cancel.html", details.group, context)


@router.get("/b/m/{public_ref}/confirm", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def confirm_page(request: Request, public_ref: str, locale: Locale) -> Response:
    """Show the appointment with one button to confirm it; a GET never confirms (ADR 0056)."""
    details = await _details(request, public_ref)
    if not details.booking.awaits_confirmation:
        return RedirectResponse(f"/b/m/{public_ref}", status_code=303)
    context = _page_context(details, locale, public_ref, None, _clock(request).now())
    return await participant_page(request, locale, "confirm.html", details.group, context)


@router.post("/b/m/{public_ref}/confirm", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def confirm(request: Request, public_ref: str, locale: Locale, _: SameOrigin) -> Response:
    """Confirm the booking; one that came too late lapses and says so."""
    ref = await authorise(request, public_ref)
    booking = await _service(request).confirm(ref)
    status = "lapsed" if booking.lapsed_unconfirmed else "confirmed"
    return RedirectResponse(_manage_url(public_ref, status), status_code=303)


@router.get("/b/m/{public_ref}/proposal", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def proposal_page(request: Request, public_ref: str, locale: Locale) -> Response:
    """Show the proposed new time with one button to take it; a GET never moves (ADR 0057)."""
    details = await _details(request, public_ref)
    if details.proposal is None:
        return RedirectResponse(f"/b/m/{public_ref}", status_code=303)
    context = _page_context(details, locale, public_ref, None, _clock(request).now())
    return await participant_page(request, locale, "proposal.html", details.group, context)


@router.post("/b/m/{public_ref}/proposal", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def confirm_proposal(
    request: Request, public_ref: str, locale: Locale, _: SameOrigin
) -> Response:
    """Take the proposed time: the booking moves there with a new reference, as a reschedule.

    A proposal answered, withdrawn or past its deadline, or a proposed slot that can no longer
    take the booking (a closure or a schedule change since), sends the browser back to the
    booking page, which says so and still offers to choose a time.
    """
    ref = await authorise(request, public_ref)
    try:
        receipt = await _service(request).confirm_proposal(ref)
    except DomainError as error:
        if error.code is ErrorCode.DATABASE_BUSY:  # a retry may still work: not "gone"
            raise
        return RedirectResponse(_manage_url(public_ref, "proposal_gone"), status_code=303)
    new = receipt.booking
    return await start(request, new.id or 0, _manage_url(new.public_ref, "rescheduled"))


def _change_service(request: Request) -> ChangeRequestService:
    """Return the application's change request service."""
    service: ChangeRequestService = request.app.state.change_request_service
    return service


@router.get("/b/m/{public_ref}/change", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def change_page(request: Request, public_ref: str, locale: Locale) -> Response:
    """Show a bot's change request with one button; a GET never changes (ADR 0023 step 2)."""
    ref = await authorise(request, public_ref)
    found = await _change_service(request).open_request(ref)
    if found is None:
        return RedirectResponse(_manage_url(public_ref, "change_gone"), status_code=303)
    details, change = found.details, found.request
    follow(request, details.group.organisation_id)
    group, booking = details.group, details.booking
    target = change.target_start
    context = {
        **_page_context(details, locale, public_ref, None, _clock(request).now()),
        "change_label": slot_label(target, target + (booking.end - booking.start), group, locale)
        if target
        else None,
        "change_by": _moment(change.expires_at, group, locale),
    }
    return await participant_page(request, locale, "change_request.html", group, context)


@router.post("/b/m/{public_ref}/change", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def confirm_change(
    request: Request, public_ref: str, locale: Locale, _: SameOrigin
) -> Response:
    """Apply the bot's change request as the participant's own cancel or move.

    A request no longer open, or a change the booking no longer allows (the time was taken,
    the deadline passed), sends the browser back to the booking page, which says so.
    """
    ref = await authorise(request, public_ref)
    try:
        done = await _change_service(request).confirm(ref)
    except DomainError as error:
        if error.code is ErrorCode.DATABASE_BUSY:  # a retry may still work: not "gone"
            raise
        return RedirectResponse(_manage_url(public_ref, "change_gone"), status_code=303)
    if done.moved is not None:
        new = done.moved.booking
        return await start(request, new.id or 0, _manage_url(new.public_ref, "rescheduled"))
    return RedirectResponse(_manage_url(public_ref, "cancelled"), status_code=303)


@router.post("/b/m/{public_ref}/cancel", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def cancel(request: Request, public_ref: str, locale: Locale, _: SameOrigin) -> Response:
    """Cancel the booking."""
    ref = await authorise(request, public_ref)
    await _service(request).cancel(ref)
    return RedirectResponse(_manage_url(public_ref, "cancelled"), status_code=303)


async def _reschedule_response(
    request: Request,
    public_ref: str,
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
    details = await _details(request, public_ref)
    group = details.group
    if not details.changeable:
        return RedirectResponse(f"/b/m/{public_ref}", status_code=303)
    context = _page_context(details, locale, public_ref, None, _clock(request).now())
    context["errors"] = errors or []
    chosen = parse_start(start)
    subject = details.booking.subject  # ADR 0081: a move keeps the subject
    context["suggestions"] = []
    if details.booking.awaits_rebook:  # ADR 0057 point 8: the next best times, not held
        suggestions = await service.suggestions(await authorise(request, public_ref))
        length = details.booking.end - details.booking.start
        context["suggestions"] = [
            (slot_value(at), slot_label(at, at + length, group, locale)) for at in suggestions
        ]
    moving = details.booking  # ADR 0122: its own slot decides where it can move
    slot = await _available(service, group, chosen, booking=moving) if chosen else None
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
            f"/b/m/{public_ref}/reschedule",
            month=month,
            day=day,
            exclude=details.booking.start,
            slot_filter=replace(parse_filter(request.query_params.multi_items()), subject=subject),
            booking=moving,
        )
        name = "reschedule.html"
    return await participant_page(request, locale, name, group, context, status_code)


async def _details_response(
    request: Request,
    public_ref: str,
    locale: RequestLocale,
    values: dict[str, str] | None = None,
    error: DomainError | None = None,
) -> Response:
    """Render the correction form, prefilled from the booking or with what was sent.

    Goes back to the booking page when it can no longer be corrected.
    """
    details = await _details(request, public_ref)
    contact = details.booking.contact
    if not details.correctable or contact is None:
        return RedirectResponse(f"/b/m/{public_ref}", status_code=303)
    service = _service(request)
    form = await load_contact_form(service, details.group, _clock(request).now())
    errors = form.errors(error, locale) if error is not None else []
    context = _page_context(details, locale, public_ref, None, _clock(request).now())
    context |= {
        "fields": form.correction_fields(locale, errors, values or contact_values(contact)),
        "errors": errors,
        "email": contact.email,
        "honeypot": HONEYPOT_FIELD,
    }
    status = status_for(error) if error is not None else 200
    return await participant_page(request, locale, "details.html", details.group, context, status)


@router.get("/b/m/{public_ref}/details", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def details_page(request: Request, public_ref: str, locale: Locale) -> Response:
    """Show the correction form for name, phone and answers (ADR 0056 point 10)."""
    return await _details_response(request, public_ref, locale)


@router.post("/b/m/{public_ref}/details", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def correct_details(
    request: Request, public_ref: str, locale: Locale, _: SameOrigin
) -> Response:
    """Correct the details; the email address stays, another one is another booking."""
    ref = await authorise(request, public_ref)
    values = read_contact(await request.form())
    try:
        await _service(request).correct_details(ref, contact_input(values))
    except DomainError as error:  # one no longer correctable goes back to the booking page
        return await _details_response(request, public_ref, locale, values, error)
    return RedirectResponse(_manage_url(public_ref, "details"), status_code=303)


@router.get("/b/m/{public_ref}/reschedule", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def reschedule_page(
    request: Request,
    public_ref: str,
    locale: Locale,
    month: str | None = None,
    day: str | None = None,
    start: str | None = None,
) -> Response:
    """Show the picker for a new time, or confirm the chosen one (``?start=``)."""
    return await _reschedule_response(
        request, public_ref, locale, month=month, day=day, start=start
    )


@router.post("/b/m/{public_ref}/reschedule", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def reschedule(request: Request, public_ref: str, locale: Locale, _: SameOrigin) -> Response:
    """Move the booking; the new booking gets a new reference and the browser holds it too."""
    ref = await authorise(request, public_ref)
    form = await request.form()
    start_at = parse_start(str(form.get("start", "")))
    if start_at is None:
        raise RejectedRequest(400)
    try:
        receipt = await _service(request).reschedule(ref, start_at)
    except DomainError as error:
        return await _reschedule_response(
            request,
            public_ref,
            locale,
            day=start_at.date().isoformat(),
            errors=form_errors(error, locale.gettext),
            status_code=status_for(error),
        )
    new = receipt.booking
    return await start(request, new.id or 0, _manage_url(new.public_ref, "rescheduled"))
