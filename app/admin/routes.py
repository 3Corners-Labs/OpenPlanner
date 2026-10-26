"""Admin portal routes (WP7a): login, overview, own rights, matrix, audit log, group view.

Every route except the login flow depends on exactly one ``RequirePermission``; the public
ones are listed in ``PUBLIC_ADMIN_PATHS`` and the route-coverage test checks both.

    GET  /admin/login        → identity provider (state, nonce, PKCE in the session)
    GET  /admin/callback     → verify, start a fresh session, audit the login
    POST /admin/logout       → end the session here and at the provider, audit the logout
    GET  /admin/             dashboard.view   groups in scope
    GET  /admin/rights       rights.view      own permissions with their scope
    GET  /admin/matrix       matrix.view      the whole matrix (same data as the docs)
    GET  /admin/audit        audit.view       audit events in scope

Groups and their planning live in ``group_routes``, organisations in
``organisation_routes`` and their branding in ``branding_routes``.
"""

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.adapters.integrations import Integrations
from app.admin import (
    branding_routes,
    call_routes,
    channel_routes,
    group_routes,
    holiday_routes,
    message_routes,
    organisation_routes,
)
from app.admin.authz import (
    AccessDenied,
    AdminContext,
    LoginRequired,
    ReauthRequired,
    RequirePermission,
    admin_service,
    session_principal,
)
from app.admin.pages import Locale, SameOrigin, templates
from app.admin.pages import message as _message
from app.admin.pages import page as _page
from app.admin.session import end_session, pop_login_start, save_login_start, start_session
from app.domain.admin import ANONYMOUS, LOGIN_EVENT, LOGOUT_EVENT
from app.domain.audit import AuditEntry, Outcome
from app.domain.authorization import (
    CITIZEN_ACTIONS,
    Permission,
    Role,
    Scope,
    permissions_for,
)
from app.domain.ports import Clock, IdentityError, IdentityProvider
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day_compact, local_day
from app.web.authz_labels import (
    CITIZEN_LABELS,
    CONDITION_LABELS,
    LEVEL_LABELS,
    OUTCOME_LABELS,
    PERMISSION_LABELS,
    ROLE_LABELS,
)
from app.web.authz_report import matrix_rows
from app.web.locale import resolve_locale
from app.web.protection import limiter, read_limit
from app.web.templates import render

router = APIRouter(prefix="/admin", include_in_schema=False)
log = structlog.get_logger()

PUBLIC_ADMIN_PATHS = frozenset(
    {"/admin/login", "/admin/callback", "/admin/logout", "/admin/logged-out"}
)
"""The only admin paths without a ``RequirePermission``: the login flow itself."""

EVENT_LABELS: dict[str, str] = {
    LOGIN_EVENT: "audit.event.login",
    LOGOUT_EVENT: "audit.event.logout",
}

DETAIL_LABELS: dict[str, str] = {
    "rule.add": "admin.audit.detail.rule_add",
    "rule.remove": "admin.audit.detail.rule_remove",
    "override.add": "admin.audit.detail.override_add",
    "override.remove": "admin.audit.detail.override_remove",
    "name": "admin.field.name",
    "timezone": "admin.field.timezone",
    "slot_minutes": "admin.field.slot_minutes",
    "min_notice_minutes": "admin.field.min_notice_minutes",
    "horizon_days": "admin.field.horizon_days",
    "phone_mode": "admin.field.phone_mode",
    "dispatch_lead_minutes": "admin.field.dispatch_lead_minutes",
    "video_host_email": "admin.field.video_host_email",
    "wxcc_business_address": "admin.field.wxcc_business_address",
    "wxcc_channel": "admin.field.wxcc_channel",
    "wxcc_routing_value": "admin.field.wxcc_routing_value",
    "direct_call_enabled": "admin.field.direct_call_enabled",
    "not_picked_up_minutes": "admin.field.not_picked_up_minutes",
    "display_name": "admin.branding.display_name",
    "accent_color": "admin.branding.accent_color",
    "logo": "admin.branding.logo",
    "logo.remove": "admin.audit.detail.logo_remove",
}
"""Readable label per audit detail key (changed field names and schedule actions)."""

OUTCOME_TONES: dict[Outcome, str] = {
    Outcome.SUCCESS: "success",
    Outcome.DENIED: "danger",
    Outcome.FAILURE: "warning",
}
"""Chip colour per audit outcome; the outcome is always also written out (WCAG 1.4.1)."""


def _identity_provider(request: Request) -> IdentityProvider | None:
    """Return the configured identity provider, or ``None`` when admin login is disabled."""
    provider: IdentityProvider | None = request.app.state.identity_provider
    return provider


def _clock(request: Request) -> Clock:
    """Return the application's clock."""
    clock: Clock = request.app.state.clock
    return clock


def _callback_url(request: Request) -> str:
    """Return the redirect URI registered at the identity provider."""
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/admin/callback"


def _safe_next(value: str | None) -> str:
    """Return ``value`` if it is an admin path on this site; otherwise the dashboard."""
    if (
        value
        and value.startswith("/admin")
        and not value.startswith("//")
        and "\\" not in value
        and "://" not in value
    ):
        return value
    return "/admin/"


@router.get("/login", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def login(request: Request, locale: Locale, next: str | None = None) -> Response:  # noqa: A002
    """Send the admin to the identity provider."""
    provider = _identity_provider(request)
    if provider is None:
        return _message(
            request, locale, 503, "admin.not_configured.title", "admin.not_configured.text"
        )
    try:
        start = await provider.start_login(_callback_url(request))
    except IdentityError:
        return _message(request, locale, 503, "admin.login_failed.title", "admin.login_failed.text")
    save_login_start(request, start, _safe_next(next))
    return RedirectResponse(start.url, status_code=303)


@router.get("/callback", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def callback(
    request: Request, locale: Locale, code: str | None = None, state: str | None = None
) -> Response:
    """Complete the login: check state, verify the token, start a fresh session."""
    provider = _identity_provider(request)
    service = admin_service(request)
    pending = pop_login_start(request)
    if (
        provider is None
        or pending is None
        or not code
        or not state
        or not hmac.compare_digest(state, pending.get("state", ""))
    ):
        await service.record(ANONYMOUS, LOGIN_EVENT, Outcome.FAILURE, "invalid_state")
        return _message(request, locale, 400, "admin.login_failed.title", "admin.login_failed.text")
    try:
        principal = await provider.finish_login(
            code=code,
            redirect_uri=_callback_url(request),
            code_verifier=pending["verifier"],
            nonce=pending["nonce"],
        )
    except IdentityError as error:
        await service.record(ANONYMOUS, LOGIN_EVENT, Outcome.FAILURE, error.code)
        return _message(request, locale, 401, "admin.login_failed.title", "admin.login_failed.text")
    start_session(request, principal, _clock(request).now())
    await service.record_login(principal)
    log.info("admin.login", actor=principal.subject, roles=sorted(principal.roles))
    return RedirectResponse(_safe_next(pending.get("next")), status_code=303)


@router.post("/logout", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def logout(request: Request, locale: Locale, _: SameOrigin) -> Response:
    """End the admin session here and at the identity provider (no silent re-login)."""
    principal = session_principal(request)
    end_session(request)
    if principal is not None:
        await admin_service(request).record_logout(principal)
    logged_out = f"{request.app.state.settings.public_base_url.rstrip('/')}/admin/logged-out"
    provider = _identity_provider(request)
    provider_logout = await provider.logout_url(logged_out) if provider else None
    if provider_logout is None:
        return RedirectResponse("/admin/logged-out", status_code=303)
    # A redirect from a form POST to the provider would be blocked by CSP form-action 'self';
    # a page that navigates on (meta refresh, plus a visible link) keeps the CSP strict.
    return render(
        templates, request, "admin/logout_redirect.html", locale, {"logout_url": provider_logout}
    )


@router.get("/logged-out", response_class=HTMLResponse)
async def logged_out(request: Request, locale: Locale) -> HTMLResponse:
    """Confirm the logout."""
    return _message(request, locale, 200, "admin.logged_out.title", "admin.logged_out.text")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
) -> HTMLResponse:
    """List the groups in the admin's scope."""
    groups = await admin_service(request).groups(context.principal)
    return _page(request, locale, "admin/dashboard.html", context.principal, {"groups": groups})


def _scope_text(scope: Scope | None, _: BoundGettext) -> str:
    """Describe a scope in words for the rights page."""
    if scope is None:
        return _("admin.rights.scope_portal")
    if scope.everything:
        return _("admin.rights.scope_everything")
    parts = []
    if scope.organisations:
        parts.append(
            _("admin.rights.scope_organisations", names=", ".join(sorted(scope.organisations)))
        )
    if scope.groups:
        parts.append(_("admin.rights.scope_groups", names=", ".join(sorted(scope.groups))))
    return "; ".join(parts)


@router.get("/rights", response_class=HTMLResponse)
async def rights(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))],
) -> HTMLResponse:
    """Show the admin's own permissions and where they apply."""
    _ = locale.gettext
    principal = context.principal
    rows = [
        (_(PERMISSION_LABELS[permission]), _scope_text(scope, _))
        for permission, scope in permissions_for(principal)
    ]
    roles = [_(ROLE_LABELS[role]) for role in Role if role in principal.roles]
    return _page(request, locale, "admin/rights.html", principal, {"rights": rows, "roles": roles})


@dataclass(frozen=True)
class MatrixRowView:
    """A translated matrix row."""

    label: str
    level: str
    granted: list[bool]


@router.get("/matrix", response_class=HTMLResponse)
async def matrix(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.MATRIX_VIEW))],
) -> HTMLResponse:
    """Show the whole matrix, generated from the same code as the documentation."""
    _ = locale.gettext
    rows = [
        MatrixRowView(
            _(PERMISSION_LABELS[row.permission]),
            _(LEVEL_LABELS[row.level]),
            [row.granted[role] for role in Role],
        )
        for row in matrix_rows()
    ]
    citizen = [
        (_(CITIZEN_LABELS[action.key]), _(CONDITION_LABELS[action.condition]))
        for action in CITIZEN_ACTIONS
    ]
    return _page(
        request,
        locale,
        "admin/matrix.html",
        context.principal,
        {"rows": rows, "role_columns": [_(ROLE_LABELS[r]) for r in Role], "citizen": citizen},
    )


@dataclass(frozen=True)
class AuditView:
    """A translated audit event."""

    when: str
    actor: str
    action: str
    outcome: str
    target: str
    tone: str
    detail: str


def _actor_label(entry: AuditEntry, _: BoundGettext) -> str:
    """Name the actor: display name, "not signed in", or the subject as a last resort."""
    if entry.event.actor == ANONYMOUS:
        return _("audit.actor.anonymous")
    return entry.actor_name or entry.event.actor


def _detail_label(detail: str, _: BoundGettext) -> str:
    """Translate an audit detail such as ``horizon_days,name`` into readable labels."""
    keys = [key for key in detail.split(",") if key]
    return ", ".join(_(DETAIL_LABELS[key]) if key in DETAIL_LABELS else key for key in keys)


def _moment(at: datetime, tz: ZoneInfo, locale: str) -> str:
    """Return a compact local date and time, e.g. ``5 jan 2026, 09:00``."""
    return f"{format_day_compact(local_day(at, tz), locale)}, {format_clock(at, tz, locale)}"


def _action_label(action: str, _: BoundGettext) -> str:
    """Translate a permission; event keys such as ``admin.login`` have their own labels."""
    if action in EVENT_LABELS:
        return _(EVENT_LABELS[action])
    try:
        return _(PERMISSION_LABELS[Permission(action)])
    except ValueError:
        return action


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
) -> HTMLResponse:
    """Show the newest audit events about organisations and groups in scope."""
    _ = locale.gettext
    tz = ZoneInfo(request.app.state.settings.admin_timezone)
    events = await admin_service(request).audit_log(context.principal)
    views = [
        AuditView(
            _moment(e.at, tz, locale.code),
            _actor_label(entry, _),
            _action_label(e.action, _),
            _(OUTCOME_LABELS[e.outcome]),
            " / ".join(t for t in (e.organisation, e.group) if t) or "–",
            OUTCOME_TONES[e.outcome],
            _detail_label(e.detail, _),
        )
        for entry in events
        for e in (entry.event,)
    ]
    return _page(request, locale, "admin/audit.html", context.principal, {"events": views})


@dataclass(frozen=True)
class IntegrationView:
    """What the integrations page shows; never a secret or token value."""

    service_app: bool
    access_expires: str | None
    refresh_expires: str | None
    refresh_expiring_soon: bool
    wxcc: bool
    webex_webhook: str
    webex_secret: bool
    wxcc_webhook: str
    wxcc_secret: bool


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.INTEGRATIONS_MANAGE))],
) -> HTMLResponse:
    """Show whether Webex and WxCC are connected, token expiry and the webhook URLs."""
    settings = request.app.state.settings
    wired: Integrations = request.app.state.integrations
    health = await wired.tokens.health() if wired.tokens else None
    tz = ZoneInfo(settings.admin_timezone)

    def moment(value: datetime | None) -> str | None:
        return _moment(value, tz, locale.code) if value else None

    base = settings.webhook_base
    view = IntegrationView(
        service_app=wired.video is not None,
        access_expires=moment(health.access_expires_at) if health else None,
        refresh_expires=moment(health.refresh_expires_at) if health else None,
        refresh_expiring_soon=bool(health and health.refresh_expiring_soon),
        wxcc=wired.dispatcher is not None,
        webex_webhook=f"{base}/webhooks/webex",
        webex_secret=settings.webex_webhook_secret is not None,
        wxcc_webhook=f"{base}/webhooks/wxcc",
        wxcc_secret=settings.wxcc_webhook_secret is not None,
    )
    groups = await admin_service(request).groups(context.principal)
    service = channel_routes.channel_service(request)
    channels = [
        await service.overview(context.principal, organisation.slug)
        for organisation in await service.organisations(context.principal)
    ]
    return _page(
        request,
        locale,
        "admin/integrations.html",
        context.principal,
        {"view": view, "groups": groups, "channels": channels},
    )


def register_admin(app: FastAPI) -> None:
    """Install the admin routes and turn ``LoginRequired``/``AccessDenied`` into responses."""

    async def login_required(request: Request, exc: Exception) -> Response:
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/admin/login?next={target}", status_code=303)

    async def access_denied(request: Request, exc: Exception) -> Response:
        locale = resolve_locale(request)
        principal = session_principal(request)
        if principal is not None and not permissions_for(principal):
            return _message(
                request, locale, 403, "admin.forbidden.title", "admin.no_role.text", back_link=False
            )
        return _message(request, locale, 403, "admin.forbidden.title", "admin.forbidden.text")

    async def reauth_required(request: Request, exc: Exception) -> Response:
        next_path = exc.next_path if isinstance(exc, ReauthRequired) else "/admin/"
        target = quote(_safe_next(next_path), safe="/")
        return RedirectResponse(f"/admin/login?next={target}", status_code=303)

    handlers: dict[type[Exception], Callable[[Request, Exception], object]] = {
        LoginRequired: login_required,
        AccessDenied: access_denied,
        ReauthRequired: reauth_required,
    }
    for exc_type, handler in handlers.items():
        app.add_exception_handler(exc_type, handler)  # type: ignore[arg-type]
    app.include_router(router)
    app.include_router(group_routes.router)
    app.include_router(call_routes.router)
    app.include_router(branding_routes.router)
    app.include_router(holiday_routes.router)
    app.include_router(channel_routes.router)
    app.include_router(message_routes.router)
    app.include_router(organisation_routes.router)
