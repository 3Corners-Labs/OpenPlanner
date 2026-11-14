"""The participant's call page (ADR 0123): ``GET /b/m/{ref}/call``, the third kind of page.

This route is the one place that serves the call page's response policy, which lets the page
reach the call's hosts and use camera, microphone and screen sharing (``app.web.security_policy``,
applied by ``participant_call_page``). It exists only while ``embedded_call_enabled`` is on;
while it is off the route answers as an address that does not exist, so nothing about the
feature shows on an installation that does not use it.

In this step the page is the shell: the appointment and one line saying the call in this page is
not available yet. It loads no script and contacts nobody; the page's way in is step 4b.

    GET  /b/m/{ref}/call         the call page (404 while the feature is off)
    POST /b/m/{ref}/call/token   the guest token for the click that joins (ADR 0123 point 6)

The token endpoint (step 4a) answers JSON with ``token``, ``expires_at`` (UTC, ISO 8601) and
``link`` (the meeting's guest link, never the host's). Every answer the endpoint builds carries
``Cache-Control: no-store``; the 403 and 404 pages are the site's own, as for every page under the
link. It needs the participant's session for the booking and the form protection of every
participant post, answers only inside the join window of ``/join/go`` (``join_state``), and is
limited per address (the common read limit) and per booking, counted for an open booking only
(``embedded_call_token_per_booking_per_hour``). The token and the links are never stored, logged,
put in a URL, an event or a job. Answers:

    200  the token
    403  no session for this booking, or a cross-site post (a booking that does not exist or is
         not theirs answers alike)
    404  the switch is off (an address that does not exist)
    409  ``{"error": "not_open"}``: not confirmed, not in the window, or no meeting
    429  ``{"error": "rate_limited"}`` with ``Retry-After``, from either limit
    503  ``{"error": "unavailable"}``: the platform refused or cannot be reached


Until step 6 gives a service its ``join_client = embedded`` the endpoint is dark behind the
platform switch alone: any booking of an installation that turned the switch on may ask, which
step 6 narrows to a service that uses the call page.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, Response

from app.api.labels import slot_label
from app.api.link_session import authorise
from app.api.participant import participant_call_page
from app.domain.call_token_service import (
    REFUSAL_STATUS,
    CallToken,
    CallTokenService,
    TokenRefusal,
)
from app.domain.guest_access import GUEST_TERM_KEY, DisplayNameMode
from app.domain.services import BookingService
from app.web.error_pages import refuse_rate_limited
from app.web.locale import resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import (
    limiter,
    read_limit,
    require_same_origin,
    within_call_token_limit,
)

router = APIRouter(include_in_schema=False)

SameOrigin = Annotated[None, Depends(require_same_origin)]

NO_STORE = {"cache-control": "no-store"}
"""A token, or the answer that there is none, is never kept by a browser or a proxy."""


@router.get("/b/m/{public_ref}/call", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def call_page(request: Request, public_ref: str) -> HTMLResponse:
    """Show the call page of the participant's booking.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at.
    """
    if not request.app.state.settings.embedded_call_enabled:
        raise HTTPException(status_code=404)
    bookings: BookingService = request.app.state.booking_service
    locale = resolve_locale(request)
    details = await bookings.find(await authorise(request, public_ref))
    follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    context = {
        "group": group,
        "ref": public_ref,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
    }
    return await participant_call_page(request, locale, "call.html", group, context)


@router.post("/b/m/{public_ref}/call/token")
@limiter.limit(read_limit)
async def call_token(request: Request, public_ref: str, _: SameOrigin) -> Response:
    """Hand the call page a guest token for the participant's click that joins.

    Only the participant's session for this booking may ask; whether the booking exists or
    belongs to someone else is not told (the same not-found answer as every page under the
    link). Nothing of the booking goes to the video platform but the guest's subject and name.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at.
    """
    settings = request.app.state.settings
    if not settings.embedded_call_enabled:
        raise HTTPException(status_code=404)
    ref = await authorise(request, public_ref)
    bookings: BookingService = request.app.state.booking_service
    details = await bookings.find(ref)  # the one read: the service takes the booking from here
    current = follow(request, details.group.organisation_id)
    term = (
        resolve_locale(request)
        .for_scope(details.group.organisation_id, details.group.id)
        .gettext(GUEST_TERM_KEY)
    )
    service: CallTokenService = request.app.state.call_token_service
    outcome = await service.issue(
        details.booking,
        DisplayNameMode(current.embedded_call_display_name),
        term,
        lambda: within_call_token_limit(public_ref),
    )
    if outcome is TokenRefusal.RATE_LIMITED:
        return await refuse_rate_limited(request)
    if isinstance(outcome, TokenRefusal):
        return JSONResponse(
            {"error": outcome.value}, status_code=REFUSAL_STATUS[outcome], headers=NO_STORE
        )
    return _answer(outcome)


def _answer(token: CallToken) -> JSONResponse:
    """Return the token's answer; the one place a token leaves the server."""
    return JSONResponse(
        {
            "token": token.access.token,
            "expires_at": token.access.expires_at.isoformat(),
            "link": token.link,
        },
        headers=NO_STORE,
    )
