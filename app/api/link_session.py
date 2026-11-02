"""The participant link session as the booking pages use it (ADR 0093).

    GET /b/{token}  ── token checked ── ``start`` ── Set-Cookie ``op_link`` ──► 303 /b/m/{ref}
    GET /b/m/{ref}  ── ``authorise``: the cookie's session holds this booking? ──► the page

The cookie holds a random value only; the server keeps its hash and the bookings the browser
may use. It proves possession of the link, not an identity. A request without a cookie that
holds the booking gets the same answer whether or not the booking exists (``link_required``),
and draws on the budget for links that match nothing.
"""

from datetime import timedelta

from fastapi import Request
from starlette.responses import RedirectResponse

from app.domain.errors import DomainError, ErrorCode
from app.domain.participant_link_service import ParticipantLinkService
from app.domain.participant_links import PublicRef
from app.web.cookies import CookieSpec, cookie_for, set_cookie

COOKIE_BASE = "op_link"
"""The cookie's name without the ``__Host-`` prefix (ADR 0093 point 2)."""

PLAIN_PATH = "/b"
"""The path over plain http (development); the prefixed name requires ``/``."""


def link_cookie(request: Request) -> CookieSpec:
    """Return the participant cookie for the address the installation is served on."""
    return cookie_for(request.app.state.settings.public_base_url, COOKIE_BASE, PLAIN_PATH)


def lifetime(request: Request) -> timedelta:
    """Return the idle lifetime of a session now in force (class C, read per request)."""
    hours: int = request.app.state.settings.participant_link_session_hours
    return timedelta(hours=hours)


def service(request: Request) -> ParticipantLinkService:
    """Return the application's participant link sessions."""
    found: ParticipantLinkService = request.app.state.participant_link_service
    return found


async def authorise(request: Request, public_ref: str) -> PublicRef:
    """Return the booking named by ``public_ref`` if the cookie's session holds it.

    Raises:
        DomainError: ``link_required`` without a live session that holds the booking; the
            same answer for a booking that does not exist.
    """
    value = request.cookies.get(link_cookie(request).name)
    if not await service(request).allows(value, public_ref, lifetime(request)):
        raise DomainError(ErrorCode.LINK_REQUIRED)
    return PublicRef(public_ref)


async def start(request: Request, booking_id: int, target: str) -> RedirectResponse:
    """Let this browser hold the booking and send it on to ``target`` (``303``).

    Args:
        request: The request that brought a checked token, or created the booking.
        booking_id: The booking the browser may use from now on.
        target: The page without a credential in its address, e.g. ``/b/m/{ref}``.
    """
    spec = link_cookie(request)
    value = await service(request).admit(
        booking_id, request.cookies.get(spec.name), lifetime(request)
    )
    response = RedirectResponse(target, status_code=303)
    response.headers["cache-control"] = "no-store"  # it carries a cookie
    set_cookie(response, spec, value, int(lifetime(request).total_seconds()))
    return response
