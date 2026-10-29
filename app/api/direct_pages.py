"""Direct video calls for citizens (ADR 0019): ``/g/{slug}/direct`` and the waiting page.

Starting a call creates a booking of kind direct, then the meeting and the work item right
away so the citizen does not wait for the worker; if Webex or WxCC is slow or down, the
worker's jobs take over (they are idempotent). The waiting page says honestly when no agent
is available. It never reloads by itself (no time limits, WCAG 2.2.1); a button refreshes the
status, and the citizen can wait in the Webex lobby meanwhile. When the group's cap of open
direct calls is reached the form says all agents are busy; the form and the waiting page show
the place in line and the usual wait from OpenPlanner's own data (ADR 0026).

    GET  /g/{slug}/direct   form (or "not possible now")
    POST /g/{slug}/direct   start, then 303 to the waiting page
    GET  /b/{token}/wait    status: looking for an agent / accepted / in the call / no agent
"""

from datetime import datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.citizen import citizen_page
from app.api.contact_form import HONEYPOT_FIELD, contact_fields, read_contact
from app.api.trace import bind_group_from_path
from app.domain.booking import BookingKind
from app.domain.call_service import CallService
from app.domain.calls import CallStatus, JoinState, TaskState, join_state, not_picked_up
from app.domain.direct_queue import DirectAvailability
from app.domain.errors import DomainError
from app.domain.integration import IntegrationService
from app.domain.ports import Clock, IntegrationError
from app.domain.scheduling import Group
from app.domain.services import BookingService, ContactInput
from app.web.errors import FormError, form_errors, status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import follow, follow_group_from_path
from app.web.protection import booking_limit, limiter, read_limit, require_same_origin

router = APIRouter(
    include_in_schema=False,
    dependencies=[Depends(bind_group_from_path), Depends(follow_group_from_path)],
)
log = structlog.get_logger()

Locale = Annotated[RequestLocale, Depends(resolve_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


def _bookings(request: Request) -> BookingService:
    """Return the booking service."""
    service: BookingService = request.app.state.booking_service
    return service


async def _form_page(
    request: Request,
    locale: RequestLocale,
    group: Group,
    *,
    availability: DirectAvailability,
    errors: list[FormError] | None = None,
    values: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the direct-call form with the queue, or the page saying it is not possible now."""
    errors = errors or []
    available = availability is DirectAvailability.AVAILABLE
    context: dict[str, object] = {
        "group": group,
        "availability": availability.value,
        "queue": await _bookings(request).direct_queue(group) if available else None,
        "fields": contact_fields(group, locale, errors, values or {}),
        "errors": errors,
        "honeypot": HONEYPOT_FIELD,
    }
    return await citizen_page(request, locale, "direct.html", group, context, status)


@router.get("/g/{slug}/direct", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def direct_form(request: Request, slug: str, locale: Locale) -> HTMLResponse:
    """Show the form to start a call now."""
    service = _bookings(request)
    group = await service.get_group(slug)
    return await _form_page(
        request, locale, group, availability=await service.direct_call_availability(slug)
    )


@router.post("/g/{slug}/direct", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def start_direct(request: Request, slug: str, locale: Locale, _: SameOrigin) -> Response:
    """Start the call: booking, meeting and work item; then the waiting page."""
    service = _bookings(request)
    group = await service.get_group(slug)
    values = read_contact(await request.form())
    try:
        receipt = await service.start_direct(
            group_slug=slug,
            contact=ContactInput(values["name"], values["email"], values["phone"] or None),
            locale=locale.code,
        )
    except DomainError as error:
        return await _form_page(
            request,
            locale,
            group,
            availability=await service.direct_call_availability(slug),
            errors=form_errors(error, locale.gettext),
            values=values,
            status=status_for(error),
        )
    calls: CallService = request.app.state.call_service
    try:
        booking = await calls.ensure_meeting(receipt.booking, group)
        await calls.ensure_task(booking, group)
    except (IntegrationError, DomainError) as exc:  # the worker's jobs will retry
        log.warning("direct.deferred", booking_id=receipt.booking.id, error=type(exc).__name__)
    return RedirectResponse(f"/b/{receipt.management_token}/wait", status_code=303)


def _wait_state(status: CallStatus, group: Group, start: datetime, now: datetime) -> str:
    """Return what the waiting page tells the citizen."""
    limit = timedelta(minutes=group.not_picked_up_minutes)
    if status.task is TaskState.FAILED or not_picked_up(status, start, now, limit):
        return "unavailable"
    if status.host_joined:
        return "in_call"
    if status.task is TaskState.CONNECTED:
        return "accepted"
    return "searching"


@router.get("/b/{token}/wait", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def wait_page(request: Request, token: str, locale: Locale) -> Response:
    """Show the status of a direct call, with the join button while it is open."""
    details = await _bookings(request).find(token)
    follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    if booking.kind is not BookingKind.DIRECT or booking.id is None:
        return RedirectResponse(f"/b/{token}", status_code=303)
    clock: Clock = request.app.state.clock
    now = clock.now()
    integration: IntegrationService = request.app.state.integration_service
    status = await integration.status(booking.id)
    state = "inactive" if not booking.is_active else _wait_state(status, group, booking.start, now)
    joinable = state != "unavailable" and join_state(booking, now) is JoinState.OPEN
    context = {
        "group": group,
        "token": token,
        "state": state,
        "queue": (
            await _bookings(request).direct_queue(group, booking.id)
            if state == "searching"
            else None
        ),
        "joinable": joinable,
        "refresh": state in ("searching", "accepted"),
    }
    return await citizen_page(request, locale, "wait.html", group, context)
