"""Citizen pages and machine-API writes during maintenance (ADR 0038 §2, MM-3).

A fixed list of rules, keyed by route template, decides what closes while maintenance is on.
``app.main`` installs ``citizen_gate`` on every citizen router and ``machine_gate`` on the
machine API, and a test checks that every public route is either closed or explicitly open, so
no route can forget it.

    closed    /g/{slug} (and /book, /direct), /b/{token}/cancel, /b/{token}/reschedule
              ─► 503 + Retry-After: the maintenance page instead of the form or the write
    open      the booking page /b/{token}, the closure page /g/{slug}/closure (ADR 0037),
              waiting and join pages, health, brand assets, webhooks (they record what
              happens in running calls)
    widget    the group, availability and booking routes of the embedded widget (ADR 0035)
              ─► 503 ``{"error": "maintenance"}`` + Retry-After, readable by the host page;
              its texts, its script and the CORS preflight stay open
    machine   every request that is not a read, except the token endpoint ─► 503
              ``{"error": "maintenance"}`` + Retry-After

The page of a group shows its organisation's branding, the expected end and the contact block
(the group's own, else the organisation's). A page under a management link does not look the
token up: it shows the installation's page, so maintenance never answers whether a token is
valid.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from app.api.citizen import citizen_page, templates
from app.domain.errors import DomainError
from app.domain.maintenance_service import MaintenanceService
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.i18n.dates import format_clock, format_day, local_day
from app.web.contact_labels import contact_service, contact_view
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import settings_for
from app.web.templates import render

GROUP_ROUTES = frozenset({"/g/{slug}", "/g/{slug}/book", "/g/{slug}/direct"})
"""Citizen routes of a group that show the maintenance page instead of a form or a write."""

BOOKING_ROUTES = frozenset(
    {"/b/{token}/cancel", "/b/{token}/reschedule", "/b/{token}/test", "/b/{token}/test/go"}
)
"""Citizen routes under a management link that close (both the form and the write). The trial
call (ADR 0069) schedules a meeting at Webex, which is a write, so its page closes too."""

WIDGET_ROUTES = frozenset(
    {
        "/api/v1/widget/{org}/{group}/",
        "/api/v1/widget/{org}/{group}/availability",
        "/api/v1/widget/{org}/{group}/bookings",
    }
)
"""Routes of the embedded widget that close, as ``/g/{slug}`` does (ADR 0035)."""

OPEN_ROUTES = frozenset(
    {
        "/",
        "/api/v1/widget/texts",
        "/widget/v1/gov-booking.js",
        "/b/{token}",
        "/g/{slug}/closure",
        "/g/{slug}/closure/image",
        "/b/{token}/wait",
        "/b/{token}/join",
        "/b/{token}/join/go",
        "/healthz",
        "/readyz",
        "/o/{slug}/theme.css",
        "/o/{slug}/logo",
        "/o/{slug}/font/{role}",
        "/webhooks/webex",
        "/webhooks/wxcc",
    }
)
"""Public routes that stay open during maintenance (ADR 0038 §2, read-only or platform input)."""

MACHINE_OPEN = frozenset({"/oauth/token"})
"""Machine-API writes that stay open: a token is needed for the reads, which stay open."""

READS = frozenset({"GET", "HEAD", "OPTIONS"})


class MaintenanceClosed(Exception):  # noqa: N818 - control flow, rendered as the 503 page
    """A citizen route is closed for maintenance.

    Args:
        slug: The group of the page, when the route names one.
    """

    def __init__(self, slug: str | None) -> None:
        """Store the group slug (not personal data)."""
        super().__init__(slug or "")
        self.slug = slug


class WidgetMaintenance(Exception):  # noqa: N818 - control flow, rendered as JSON 503
    """A widget route is closed for maintenance; the widget API answers it (ADR 0035)."""


class MachineMaintenance(Exception):  # noqa: N818 - control flow, rendered as JSON 503
    """A machine-API write is refused for maintenance."""


def _maintenance(request: Request) -> MaintenanceService:
    """Return the application's maintenance service."""
    service: MaintenanceService = request.app.state.maintenance_service
    return service


def _route_path(request: Request) -> str:
    """Return the template of the matched route (``/g/{slug}/book``)."""
    return str(getattr(request.scope.get("route"), "path", ""))


async def citizen_gate(request: Request) -> None:
    """Router dependency: close the citizen routes of the rules while maintenance is on.

    Raises:
        MaintenanceClosed: For a route in ``GROUP_ROUTES`` or ``BOOKING_ROUTES``.
        WidgetMaintenance: For a route in ``WIDGET_ROUTES``, except the CORS preflight.
    """
    route = _route_path(request)
    widget = route in WIDGET_ROUTES and request.method != "OPTIONS"
    if route not in GROUP_ROUTES and route not in BOOKING_ROUTES and not widget:
        return
    if not (await _maintenance(request).status()).active:
        return
    if widget:
        raise WidgetMaintenance
    raise MaintenanceClosed(request.path_params.get("slug") if route in GROUP_ROUTES else None)


async def machine_gate(request: Request) -> None:
    """Dependency of the machine API: refuse writes while maintenance is on.

    Raises:
        MachineMaintenance: For any request that is not a read, except ``MACHINE_OPEN``.
    """
    if request.method in READS or _route_path(request) in MACHINE_OPEN:
        return
    if (await _maintenance(request).status()).active:
        raise MachineMaintenance


def retry_after(request: Request) -> dict[str, str]:
    """Return the ``Retry-After`` header of a refusal."""
    return {"retry-after": str(request.app.state.settings.maintenance_retry_after_seconds)}


def _moment(moment: datetime, tz: ZoneInfo, locale: str) -> str:
    """Return e.g. ``maandag 5 januari 2026, 09:00`` in ``tz``."""
    return f"{format_day(local_day(moment, tz), locale)}, {format_clock(moment, tz, locale)}"


async def _group(request: Request, slug: str | None) -> Group | None:
    """Return the group of the page, or ``None`` for an unknown slug or a management link."""
    if slug is None:
        return None
    service: BookingService = request.app.state.booking_service
    try:
        return await service.get_group(slug)
    except DomainError:
        return None


async def maintenance_page(request: Request, slug: str | None) -> HTMLResponse:
    """Render the maintenance page: branding, expected end and contact block of the group."""
    locale: RequestLocale = resolve_locale(request)
    status = await _maintenance(request).status()
    group = await _group(request, slug)
    end = status.state.expected_end
    tz = group.tz if group else ZoneInfo(settings_for(request).admin_timezone)
    context: dict[str, object] = {
        "expected_end": _moment(end, tz, locale.code) if end else "",
        "contacts": None,
    }
    if group is None:
        response = render(templates, request, "maintenance.html", locale, context)
        response.status_code = 503
    else:
        block = await contact_service(request).public_for_group(group)
        context["contacts"] = contact_view(block, locale)
        response = await citizen_page(request, locale, "maintenance.html", group, context, 503)
    response.headers.update(retry_after(request))
    return response


def register_maintenance(app: FastAPI) -> None:
    """Install the handler that turns ``MaintenanceClosed`` into the maintenance page."""

    async def closed(request: Request, exc: Exception) -> Response:
        slug = exc.slug if isinstance(exc, MaintenanceClosed) else None
        return await maintenance_page(request, slug)

    app.add_exception_handler(MaintenanceClosed, closed)


def register_machine_maintenance(app: FastAPI) -> None:
    """Install the machine API's JSON answer to ``MachineMaintenance``."""

    async def refused(request: Request, exc: Exception) -> Response:
        return JSONResponse(
            {"error": "maintenance"},
            status_code=503,
            headers={**retry_after(request), "cache-control": "no-store"},
        )

    app.add_exception_handler(MachineMaintenance, refused)
