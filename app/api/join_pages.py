"""The participant's way into the video call (ADR 0017): ``/b/m/{ref}/join``.

The email links here, not to Webex: the link never expires, nothing about the participant is sent
to Webex for it, and opening and clicking are recorded on the booking's timeline. The page
opens 15 minutes before the start; before that (and while the meeting is being prepared) it
shows the time and a button to check again. It never reloads by itself (WCAG 2.2.1).

    GET /b/m/{ref}/join      status page (too early / open / ended / not active)
    GET /b/m/{ref}/join/go   record the click, 303 to the meeting's Webex link

For a booking of a service that uses the call page of its own (ADR 0123, ``call_client``) the
join page's button leads to ``/b/m/{ref}/call``, and ``/join/go`` leads the participant there
too, unless the service's ``embedded_call_webex_option`` allows another way: only then is the
address that sends the participant to the video platform open for such a booking.
"""

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.labels import slot_label
from app.api.link_session import authorise
from app.api.participant import participant_page
from app.domain.call_client import (
    allows_other_way,
    call_client,
    other_way_offer,
    redirect_client,
    uses_call_page,
)
from app.domain.calls import JOIN_OPENS_BEFORE, EventKind, JoinState, join_link, join_state
from app.domain.integration import IntegrationService
from app.domain.ports import Clock
from app.domain.services import BookingService
from app.i18n.dates import format_clock
from app.web.locale import resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import limiter, read_limit

router = APIRouter(include_in_schema=False)

REFRESHING = frozenset({JoinState.TOO_EARLY, JoinState.NO_MEETING})
"""States in which the page offers a button to check again."""


def _services(request: Request) -> tuple[BookingService, IntegrationService, Clock]:
    """Return the booking service, the integration service and the clock."""
    state = request.app.state
    return state.booking_service, state.integration_service, state.clock


@router.get("/b/m/{public_ref}/join", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def join_page(request: Request, public_ref: str) -> HTMLResponse:
    """Show whether the call can be joined now, and the button when it can."""
    bookings, integration, clock = _services(request)
    locale = resolve_locale(request)
    details = await bookings.find(await authorise(request, public_ref))
    follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    state = join_state(booking, clock.now())
    client = call_client(group, platform_enabled=request.app.state.settings.embedded_call_enabled)
    if state is JoinState.OPEN and booking.id is not None:
        await integration.record(booking.id, EventKind.JOIN_PAGE_OPENED)
    context = {
        "group": group,
        "ref": public_ref,
        "state": state.value,
        "client": client.value,
        "refresh": state in REFRESHING,
        "trial_offered": group.trial_call_enabled and group.video_enabled,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
        "opens_at": format_clock(booking.start - JOIN_OPENS_BEFORE, group.tz, locale.code),
    }
    return await participant_page(request, locale, "join.html", group, context)


@router.get("/b/m/{public_ref}/join/go")
@limiter.limit(read_limit)
async def join_go(request: Request, public_ref: str) -> Response:
    """Send the participant to the meeting, or back to the status page when it is not open."""
    bookings, integration, clock = _services(request)
    details = await bookings.find(await authorise(request, public_ref))
    current = follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    # The link comes from Webex: ``join_link`` refuses anything but https and sets the group's
    # client (ADR 0075), so nothing else can ever become a redirect.
    shape = redirect_client(group)
    link = join_link(booking.meeting.link if booking.meeting else None, shape)
    if join_state(booking, clock.now()) is not JoinState.OPEN or not link or booking.id is None:
        return RedirectResponse(f"/b/m/{public_ref}/join", status_code=303)
    enabled = request.app.state.settings.embedded_call_enabled
    offer = other_way_offer(group, current.embedded_call_webex_option)
    if uses_call_page(group, platform_enabled=enabled) and not allows_other_way(offer):
        # The call is in our page; the platform's address stays closed around it (point 11).
        return RedirectResponse(f"/b/m/{public_ref}/call", status_code=303)
    await integration.record(booking.id, EventKind.JOIN_CLICKED, detail=shape.value)
    return RedirectResponse(link, status_code=303)
