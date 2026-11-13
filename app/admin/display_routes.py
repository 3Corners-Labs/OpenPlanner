"""The screen side of a display link: the one-time address, the board and its poll (step 14).

    GET /display/start/{token}   the first use: consumed, a screen cookie set, 303 to /display
    GET /display                 the wallboard, full screen, of the link's group or organisation
    GET /display/live            its fragment, polled by ``static/live-region.js``

Nothing here is an admin route: it has no admin session and none of the admin pages, and the
cookie (``op_display``) is only ever sent to ``/display`` over plain http, and means nothing to an
admin route even when the browser sends it (``__Host-`` names need ``Path=/``). Every request with
the cookie goes through ``WallboardLinkService.session``: revocation, expiry, the organisation's
switch, the ACL and the pin apply to each poll. The token is in one URL only, the first request;
the response never echoes it, redirects without it and tells the browser to send no referrer. The
route template (``/display/start/{token}``) is what the logs carry, never the raw path.
"""

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.display_link_routes import link_service
from app.admin.pages import Locale, templates
from app.admin.wallboard_routes import board_context, wallboard_service
from app.domain.errors import DomainError, ErrorCode
from app.domain.wallboard_link import Refusal, WallboardLink
from app.web.caller import client_address
from app.web.cookies import CookieSpec, cookie_for, set_cookie
from app.web.protection import limiter, unknown_link_limit
from app.web.templates import render

router = APIRouter(prefix="/display", include_in_schema=False)

COOKIE_BASE = "op_display"
"""The cookie's name without the ``__Host-`` prefix."""
PLAIN_PATH = "/display"
"""The cookie's path over plain http (development); the prefixed name requires ``/``."""
BOARD = "/display"
POLL = "/display/live"

MESSAGES: dict[str, str] = {
    Refusal.LINK.value: "reports.display.refused.link",
    Refusal.NETWORK.value: "reports.display.refused.network",
    Refusal.PIN.value: "reports.display.refused.network",
    Refusal.OFF.value: "reports.display.refused.off",
}
"""The words for each refusal; a pin and an ACL read the same, so the page says no more."""
_NO_STORE = {"cache-control": "no-store", "referrer-policy": "no-referrer"}


def display_cookie(request: Request) -> CookieSpec:
    """Return the screen cookie for the address the installation is served on."""
    return cookie_for(request.app.state.settings.public_base_url, COOKIE_BASE, PLAIN_PATH)


def _refused(request: Request, locale: Locale, error: DomainError) -> HTMLResponse:
    """Return the plain page of a refused request, 403, with the words for its code."""
    message = MESSAGES.get(error.field or "", MESSAGES[Refusal.LINK.value])
    response = render(
        templates, request, "admin/display_refused.html", locale, {"message": message}
    )
    response.status_code = 403
    for name, value in _NO_STORE.items():
        response.headers[name] = value
    return response


async def _session(request: Request) -> WallboardLink:
    """Return the link behind the request's screen cookie.

    Raises:
        DomainError: ``not_permitted`` with a ``Refusal`` for a missing or refused session.
    """
    secret = request.cookies.get(display_cookie(request).name, "")
    if not secret or len(secret) > 128:
        raise DomainError(ErrorCode.NOT_PERMITTED, Refusal.LINK.value)
    return await link_service(request).session(secret, client_address(request) or "")


@router.get("/start/{token}", response_model=None)
@limiter.limit(unknown_link_limit)
async def start(request: Request, locale: Locale, token: str) -> Response:
    """Use the link up: set the screen cookie and go to the board without the token in the URL."""
    if len(token) > 128:
        return _refused(request, locale, DomainError(ErrorCode.NOT_PERMITTED, Refusal.LINK.value))
    try:
        opened = await link_service(request).exchange(token, client_address(request) or "")
    except DomainError as error:
        if error.code is not ErrorCode.NOT_PERMITTED:
            raise
        return _refused(request, locale, error)
    response = RedirectResponse(BOARD, status_code=303, headers=_NO_STORE)
    remaining = int((opened.link.expires_at - request.app.state.clock.now()).total_seconds())
    set_cookie(response, display_cookie(request), opened.secret, max(remaining, 1))
    return response


async def _context(request: Request, locale: Locale, link: WallboardLink) -> dict[str, object]:
    """Return the board of the link's scope, ready for the kiosk templates (page and fragment)."""
    shown, covered = await wallboard_service(request).read_scope(
        link.scope(), link.organisation_slug, link.group_slug
    )
    refresh = request.app.state.live_settings.for_organisation(link.organisation_id)
    return board_context(
        request,
        locale,
        shown,
        covered,
        BOARD,
        kiosk=True,
        poll=POLL,
        here=BOARD,
        seconds=refresh.reporting_wallboard_refresh_seconds,
    )


@router.get("", response_class=HTMLResponse, response_model=None)
async def board(request: Request, locale: Locale) -> Response:
    """Show the wallboard of the link's group or organisation, full screen."""
    try:
        link = await _session(request)
    except DomainError as error:
        if error.code is not ErrorCode.NOT_PERMITTED:
            raise
        return _refused(request, locale, error)
    response = render(
        templates,
        request,
        "admin/wallboard_kiosk.html",
        locale,
        {**await _context(request, locale, link), "exit": None},
        scripts=True,
    )
    response.headers["referrer-policy"] = "no-referrer"
    return response


@router.get("/live", response_class=HTMLResponse, response_model=None)
async def live(request: Request, locale: Locale) -> Response:
    """Return the board's fragment for the poller; 403 ends the poller and says the data is old."""
    try:
        link = await _session(request)
    except DomainError as error:
        if error.code is not ErrorCode.NOT_PERMITTED:
            raise
        return Response(status_code=403, headers=_NO_STORE)
    return render(
        templates, request, "admin/_wallboard.html", locale, await _context(request, locale, link)
    )
