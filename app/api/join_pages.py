"""The citizen's way into the video call (ADR 0017): ``/b/{token}/join``.

The email links here, not to Webex: the link never expires, nothing about the citizen is sent
to Webex for it, and opening and clicking are recorded on the booking's timeline. The page
opens 15 minutes before the start; before that (and while the meeting is being prepared) it
shows the time and refreshes itself every minute without scripts.

    GET /b/{token}/join      status page (too early / open / ended / not active)
    GET /b/{token}/join/go   record the click, 303 to the meeting's Webex link
"""

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.labels import slot_label
from app.domain.calls import JOIN_OPENS_BEFORE, EventKind, JoinState, join_state
from app.domain.integration import IntegrationService
from app.domain.ports import Clock
from app.domain.services import BookingService
from app.i18n.dates import format_clock
from app.web.locale import resolve_locale
from app.web.protection import limiter, read_limit
from app.web.templates import create_templates, render

router = APIRouter(include_in_schema=False)
templates = create_templates([Path(__file__).parent / "templates"])

REFRESHING = frozenset({JoinState.TOO_EARLY, JoinState.NO_MEETING})
"""States in which the page reloads itself every minute until it can open."""


def _services(request: Request) -> tuple[BookingService, IntegrationService, Clock]:
    """Return the booking service, the integration service and the clock."""
    state = request.app.state
    return state.booking_service, state.integration_service, state.clock


@router.get("/b/{token}/join", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def join_page(request: Request, token: str) -> HTMLResponse:
    """Show whether the call can be joined now, and the button when it can."""
    bookings, integration, clock = _services(request)
    locale = resolve_locale(request)
    details = await bookings.find(token)
    booking, group = details.booking, details.group
    state = join_state(booking, clock.now())
    if state is JoinState.OPEN and booking.id is not None:
        await integration.record(booking.id, EventKind.JOIN_PAGE_OPENED)
    context = {
        "group": group,
        "token": token,
        "state": state.value,
        "refresh": state in REFRESHING,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
        "opens_at": format_clock(booking.start - JOIN_OPENS_BEFORE, group.tz, locale.code),
    }
    return render(templates, request, "join.html", locale, context)


@router.get("/b/{token}/join/go")
@limiter.limit(read_limit)
async def join_go(request: Request, token: str) -> Response:
    """Send the citizen to the meeting, or back to the status page when it is not open."""
    bookings, integration, clock = _services(request)
    details = await bookings.find(token)
    booking = details.booking
    link = booking.meeting.link if booking.meeting else None
    if join_state(booking, clock.now()) is not JoinState.OPEN or not link or booking.id is None:
        return RedirectResponse(f"/b/{token}/join", status_code=303)
    if not link.startswith("https://"):  # the link comes from Webex; never redirect elsewhere
        return RedirectResponse(f"/b/{token}/join", status_code=303)
    await integration.record(booking.id, EventKind.JOIN_CLICKED)
    return RedirectResponse(link, status_code=303)
