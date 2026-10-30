"""Direct video calls for citizens (ADR 0019): ``/g/{slug}/direct`` and the waiting page.

Starting a call creates a booking of kind direct, then the meeting and the work item right
away so the citizen does not wait for the worker; if Webex or WxCC is slow or down, the
worker's jobs take over (they are idempotent). The waiting page says honestly when no agent
is available and when the meeting could not be set up (``direct_queue.wait_state``, ADR
0070). It never reloads by itself (no time limits, WCAG 2.2.1): ``static/live-region.js``
polls a status fragment and swaps it in, and without the script a button refreshes the page;
the citizen can wait in the Webex lobby meanwhile. When the group's cap of open direct calls
is reached the form says all agents are busy; the form and the waiting page show the place
in line and the usual wait from OpenPlanner's own data (ADR 0026).

    GET  /g/{slug}/direct          form (or "not possible now")
    POST /g/{slug}/direct          start, then 303 to the waiting page
    GET  /b/{token}/wait           status: searching / accepted / in the call / no agent / failed
    GET  /b/{token}/wait/status    the same as a fragment; 204 while ``seen`` is current
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.citizen import citizen_page, templates
from app.api.contact_form import (
    HONEYPOT_FIELD,
    contact_input,
    load_contact_form,
    read_contact,
)
from app.api.trace import bind_group_from_path
from app.domain.booking import BookingKind
from app.domain.call_service import CallService
from app.domain.calls import JoinState, join_state
from app.domain.direct_queue import DirectAvailability, QueueInfo, WaitState, wait_state
from app.domain.errors import DomainError
from app.domain.event_context import bind_booking
from app.domain.integration import IntegrationService
from app.domain.ports import Clock, IntegrationError
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.web.errors import status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import follow, follow_group_from_path
from app.web.protection import booking_limit, limiter, read_limit, require_same_origin
from app.web.templates import render

router = APIRouter(
    include_in_schema=False,
    dependencies=[Depends(bind_group_from_path), Depends(follow_group_from_path)],
)
log = structlog.get_logger()

Locale = Annotated[RequestLocale, Depends(resolve_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]
Seen = Annotated[str, Query(max_length=64)]
"""The key of the status the page shows; the fragment answers 204 while it is current."""

POLLING = frozenset({WaitState.SEARCHING, WaitState.ACCEPTED})
"""States that can still change into something the citizen must see at once."""
OVER = frozenset({WaitState.UNAVAILABLE, WaitState.FAILED})
"""States in which the page offers to schedule an appointment instead."""
WAIT_TEXT: dict[WaitState, str] = {
    WaitState.SEARCHING: "wait.searching",
    WaitState.ACCEPTED: "wait.accepted",
    WaitState.IN_CALL: "wait.in_call",
    WaitState.UNAVAILABLE: "wait.unavailable",
    WaitState.FAILED: "wait.failed",
    WaitState.INACTIVE: "manage.inactive",
}
"""The one sentence per state: shown in the notice and announced in the page's status region."""


@dataclass(frozen=True)
class WaitView:
    """The waiting page's status: what the page and its fragment render."""

    group: Group
    token: str
    state: WaitState
    queue: QueueInfo | None
    joinable: bool

    @property
    def key(self) -> str:
        """A short digest of what is shown, so the poller can ask "still this?"."""
        shown = (self.state, self.joinable, self.queue)
        return hashlib.sha256(repr(shown).encode()).hexdigest()[:16]

    def context(self, request: Request, locale: RequestLocale) -> dict[str, object]:
        """Return the template context, with the poll URL while the state can still change."""
        polling = self.state in POLLING
        return {
            "group": self.group,
            "token": self.token,
            "state": self.state.value,
            "status_text": locale.gettext(WAIT_TEXT[self.state]),
            "queue": self.queue,
            "joinable": self.joinable,
            "refresh": polling,
            "over": self.state in OVER,
            "poll": f"/b/{self.token}/wait/status?seen={self.key}" if polling else None,
            "poll_seconds": follow(request, self.group.organisation_id).queue_refresh_seconds,
        }


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
    error: DomainError | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render the direct-call form with the queue, or the page saying it is not possible now."""
    service = _bookings(request)
    clock: Clock = request.app.state.clock
    contact = await load_contact_form(service, group, clock.now())
    errors = contact.errors(error, locale) if error else []
    available = availability is DirectAvailability.AVAILABLE
    context: dict[str, object] = {
        "group": group,
        "availability": availability.value,
        "queue": await service.direct_queue(group) if available else None,
        "fields": contact.fields(locale, errors, values or {}),
        "errors": errors,
        "honeypot": HONEYPOT_FIELD,
    }
    status = status_for(error) if error else 200
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
            contact=contact_input(values),
            locale=locale.code,
        )
    except DomainError as error:
        return await _form_page(
            request,
            locale,
            group,
            availability=await service.direct_call_availability(slug),
            error=error,
            values=values,
        )
    calls: CallService = request.app.state.call_service
    bind_booking(receipt.booking.id)  # ADR 0068: the request events belong to this booking
    try:
        booking = await calls.ensure_meeting(receipt.booking, group)
        await calls.ensure_task(booking, group)
    except (IntegrationError, DomainError) as exc:  # the worker's jobs will retry
        log.warning("direct.deferred", booking_id=receipt.booking.id, error=type(exc).__name__)
    return RedirectResponse(f"/b/{receipt.management_token}/wait", status_code=303)


async def _wait_view(request: Request, token: str) -> WaitView | None:
    """Load the direct call behind ``token`` and decide what its page says.

    Returns ``None`` for a scheduled booking, whose management page is the place to be.
    """
    details = await _bookings(request).find(token)
    follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    if booking.kind is not BookingKind.DIRECT or booking.id is None:
        return None
    clock: Clock = request.app.state.clock
    now = clock.now()
    integration: IntegrationService = request.app.state.integration_service
    state = wait_state(booking, await integration.status(booking.id), group, now)
    queue = (
        await _bookings(request).direct_queue(group, booking.id)
        if state is WaitState.SEARCHING
        else None
    )
    joinable = state not in OVER and join_state(booking, now) is JoinState.OPEN
    return WaitView(group, token, state, queue, joinable)


@router.get("/b/{token}/wait", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def wait_page(request: Request, token: str, locale: Locale) -> Response:
    """Show the status of a direct call, with the join button while it is open."""
    view = await _wait_view(request, token)
    if view is None:
        return RedirectResponse(f"/b/{token}", status_code=303)
    context = view.context(request, locale)
    return await citizen_page(request, locale, "wait.html", view.group, context)


@router.get("/b/{token}/wait/status", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def wait_status(request: Request, token: str, locale: Locale, seen: Seen = "") -> Response:
    """Return the status fragment for the page's poller; 204 while ``seen`` is current."""
    view = await _wait_view(request, token)
    if view is None:
        return RedirectResponse(f"/b/{token}", status_code=303)
    if view.key == seen:
        return Response(status_code=204, headers={"cache-control": "no-store"})
    return render(templates, request, "_wait_status.html", locale, view.context(request, locale))
