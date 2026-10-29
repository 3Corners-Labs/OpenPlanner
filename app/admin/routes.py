"""Admin portal routes (WP7a): login, overview, own rights, matrix, integrations.

Every route except the login flow depends on exactly one ``RequirePermission``; the public
ones are listed in ``PUBLIC_ADMIN_PATHS`` and the route-coverage test checks both.

    GET  /admin/login        → identity provider (state, nonce, PKCE in the session)
    GET  /admin/callback     → verify, start a fresh session, audit the login
    POST /admin/logout       → end the session here and at the provider, audit the logout
    GET  /admin/             dashboard.view   groups in scope
    GET  /admin/rights       rights.view      own permissions with their scope
    GET  /admin/matrix       matrix.view      the whole matrix (same data as the docs)

The audit log lives in ``audit_routes``, the retention overview in ``retention_routes``, the
staff directory in ``staff_routes``,
groups and their planning in ``group_routes``, organisations in ``organisation_routes`` and
their branding in ``branding_routes``.
"""

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore
from app.adapters.integrations import Integrations
from app.admin import (
    agenda_routes,
    api_routes,
    audit_routes,
    branding_routes,
    call_routes,
    channel_routes,
    check_routes,
    closure_routes,
    contact_routes,
    group_routes,
    help_routes,
    holiday_routes,
    message_routes,
    organisation_routes,
    planner_routes,
    retention_routes,
    simulator_routes,
    staff_routes,
    update_routes,
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
from app.admin.forms import safe_next
from app.admin.integration_views import check_view
from app.admin.pages import Locale, SameOrigin, admin_moment, templates
from app.admin.pages import message as _message
from app.admin.pages import page as _page
from app.admin.session import end_session, pop_login_start, save_login_start, start_session
from app.admin.shortfalls import agenda_service, shortfall_view
from app.domain.admin import ANONYMOUS, LOGIN_EVENT
from app.domain.audit import Outcome
from app.domain.authorization import (
    API_SCOPES,
    CITIZEN_ACTIONS,
    Permission,
    Role,
    Scope,
    permissions_for,
)
from app.domain.ports import Clock, IdentityError, IdentityProvider
from app.i18n import BoundGettext
from app.web.authz_labels import (
    API_SCOPE_LABELS,
    CITIZEN_LABELS,
    CONDITION_LABELS,
    LEVEL_LABELS,
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
    save_login_start(request, start, safe_next(next))
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
    return RedirectResponse(safe_next(pending.get("next")), status_code=303)


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
    """List the groups in the admin's scope, with their shortfalls ahead (ADR 0044)."""
    groups = await admin_service(request).groups(context.principal)
    summaries = await agenda_service(request).summaries(context.principal)
    shortfalls = {
        g.group.slug: shortfall_view(summaries[g.group.slug], g.group, locale)
        for g in groups
        if g.group.slug in summaries and summaries[g.group.slug].count
    }
    return _page(
        request,
        locale,
        "admin/dashboard.html",
        context.principal,
        {"groups": groups, "shortfalls": shortfalls},
    )


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
    locked: bool
    pii: bool


@router.get("/matrix", response_class=HTMLResponse)
async def matrix(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.MATRIX_VIEW))],
) -> HTMLResponse:
    """Show the whole matrix with its bounds: locked and personal-data permissions (ADR 0039)."""
    _ = locale.gettext
    rows = [
        MatrixRowView(
            _(PERMISSION_LABELS[row.permission]),
            _(LEVEL_LABELS[row.level]),
            [row.granted[role] for role in Role],
            row.locked,
            row.pii,
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
        {
            "rows": rows,
            "role_columns": [_(ROLE_LABELS[r]) for r in Role],
            "citizen": citizen,
            "api_scopes": [
                (f"{_(API_SCOPE_LABELS[scope])} ({scope})", _(LEVEL_LABELS[level]))
                for scope, level in API_SCOPES.items()
            ],
        },
    )


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
    """Show whether Webex and WxCC are connected, token expiry, the webhook URLs and checks.

    The last ``webhooks-list`` and ``webhooks-health`` results are shown read-only; cleaning
    up registrations stays on the command line.
    """
    settings = request.app.state.settings
    wired: Integrations = request.app.state.integrations
    health = await wired.tokens.health() if wired.tokens else None

    def moment(value: datetime | None) -> str | None:
        return admin_moment(request, locale, value) if value else None

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
    reports = ReportStore(request.app.state.engine)
    checks = check_view(
        await reports.get(REGISTRATIONS_REPORT),
        await reports.get(HEALTH_REPORT),
        locale.gettext,
        lambda at: admin_moment(request, locale, at),
    )
    return _page(
        request,
        locale,
        "admin/integrations.html",
        context.principal,
        {"view": view, "groups": groups, "channels": channels, "checks": checks},
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
        target = quote(safe_next(next_path), safe="/")
        return RedirectResponse(f"/admin/login?next={target}", status_code=303)

    handlers: dict[type[Exception], Callable[[Request, Exception], object]] = {
        LoginRequired: login_required,
        AccessDenied: access_denied,
        ReauthRequired: reauth_required,
    }
    for exc_type, handler in handlers.items():
        app.add_exception_handler(exc_type, handler)  # type: ignore[arg-type]
    # The update banner (ADR 0029) is loaded for every admin page in one place.
    banner = [Depends(update_routes.load_update_notice)]
    for module_router in (
        router,
        audit_routes.router,
        retention_routes.router,
        staff_routes.router,
        group_routes.router,
        call_routes.router,
        agenda_routes.router,
        planner_routes.router,
        check_routes.router,
        branding_routes.router,
        holiday_routes.router,
        channel_routes.router,
        message_routes.router,
        organisation_routes.router,
        closure_routes.router,
        contact_routes.router,
        api_routes.router,
        simulator_routes.router,
        update_routes.router,
        help_routes.router,
    ):
        app.include_router(module_router, dependencies=banner)
