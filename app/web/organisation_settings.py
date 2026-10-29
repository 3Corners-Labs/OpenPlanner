"""The settings of the organisation a request is about (ADR 0042 step 5).

An organisation can set its own class D values: the time format, the admin portal's time
zone, and the staff, shortfall and rebook periods. A request follows one organisation:

    citizen page of a group (/g/{slug}...)   the group's organisation (router dependency)
    management link (/b/{token}...)          the booking's group's organisation (after lookup)
    admin portal                             the admin's own organisation if exactly one;
                                             the superuser and multi-organisation admins
                                             get the platform values

``ClockScopeMiddleware`` opens a fresh clock scope per request, so an organisation's time
format never leaks into the next request; ``follow`` fills it in. ``settings_for`` returns
the settings the request follows (the platform's when it follows none).
"""

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.adapters.live_settings import LiveSettings
from app.config import Settings
from app.domain.errors import DomainError
from app.domain.scheduling import is_slug
from app.domain.services import BookingService
from app.i18n.dates import clock_scope, use_clock


class ClockScopeMiddleware:
    """Opens one clock scope per HTTP request (ASGI middleware)."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the request inside its own clock scope."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with clock_scope():
            await self.app(scope, receive, send)


def follow(request: Request, organisation_id: int | None) -> Settings:
    """Make ``request`` follow one organisation's settings and time format; return them."""
    live: LiveSettings | None = getattr(request.app.state, "live_settings", None)
    current = live.for_organisation(organisation_id) if live else settings_for(request)
    request.state.organisation_settings = current
    use_clock(current.time_format)
    return current


def settings_for(request: Request) -> Settings:
    """Return the settings ``request`` follows: an organisation's, or the platform's."""
    current: Settings = getattr(request.state, "organisation_settings", None) or (
        request.app.state.settings
    )
    return current


async def follow_group_from_path(request: Request) -> None:
    """Router dependency: follow the organisation of the path's group, if it exists.

    The group is ``{slug}`` on citizen pages and ``{group}`` in the widget API. An unknown or
    malformed slug follows nothing; the route answers it as it always did.
    """
    slug = request.path_params.get("slug") or request.path_params.get("group")
    service: BookingService | None = getattr(request.app.state, "booking_service", None)
    if not (isinstance(slug, str) and is_slug(slug)) or service is None:
        return
    try:
        group = await service.get_group(slug)
    except DomainError:
        return
    follow(request, group.organisation_id)
