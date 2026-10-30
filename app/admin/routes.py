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
staff directory in ``staff_routes``, System status and Advanced settings in ``settings_routes``,
groups and their planning in ``group_routes``, organisations in ``organisation_routes`` and
their branding in ``branding_routes``.
"""

import hmac
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
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
    closure_image_routes,
    closure_routes,
    contact_routes,
    embedding_routes,
    form_routes,
    group_routes,
    help_routes,
    holiday_routes,
    maintenance_routes,
    message_routes,
    organisation_routes,
    organisation_settings_routes,
    planner_routes,
    queue_routes,
    retention_routes,
    role_routes,
    scenario_routes,
    settings_routes,
    simulator_routes,
    staff_routes,
    update_routes,
)
from app.admin.authz import (
    AccessDenied,
    AdminContext,
    LoginRequired,
    MaintenanceRefused,
    ReauthRequired,
    RequirePermission,
    admin_service,
    load_principal,
    role_service,
    session_principal,
)
from app.admin.forms import safe_next
from app.admin.integration_views import check_view
from app.admin.pages import Locale, SameOrigin, admin_moment, role_labels, templates
from app.admin.pages import message as _message
from app.admin.pages import page as _page
from app.admin.session import (
    end_session,
    has_login_trial,
    pop_login_start,
    pop_login_trial,
    save_login_start,
    start_session,
)
from app.admin.shortfalls import agenda_service, shortfall_view
from app.domain.admin import ANONYMOUS, LOGIN_EVENT
from app.domain.audit import AuditEntry
from app.domain.authorization import (
    API_SCOPES,
    CITIZEN_ACTIONS,
    Permission,
    Principal,
    Role,
    Scope,
    permissions_for,
    role_keys,
)
from app.domain.errors import DomainError
from app.domain.ports import Clock, IdentityError, IdentityProvider
from app.domain.roles import ROLE_CREATED, ROLE_DELETED, ROLE_UPDATED
from app.domain.settings_service import SettingsService
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
from app.web.locale import RequestLocale, resolve_locale
from app.web.protection import limiter, read_limit
from app.web.security_events import report
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
async def login(
    request: Request,
    locale: Locale,
    next: str | None = None,  # noqa: A002
    fresh: bool = False,
) -> Response:
    """Send the admin to the identity provider; ``fresh`` for a mapping dry run (§8)."""
    provider = _identity_provider(request)
    if provider is None:
        return _message(
            request, locale, 503, "admin.not_configured.title", "admin.not_configured.text"
        )
    try:
        start = await provider.start_login(
            _callback_url(request), fresh=fresh and has_login_trial(request)
        )
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
    trial = pop_login_trial(
        request,
        _clock(request).now(),
        timedelta(minutes=request.app.state.settings.admin_reauth_minutes),
    )
    if (
        provider is None
        or pending is None
        or not code
        or not state
        or not hmac.compare_digest(state, pending.get("state", ""))
    ):
        await report(request, LOGIN_EVENT, "invalid_state", actor=ANONYMOUS)
        return _message(request, locale, 400, "admin.login_failed.title", "admin.login_failed.text")
    tried: Principal | None = None
    try:
        if trial is None:
            principal = await provider.finish_login(
                code=code,
                redirect_uri=_callback_url(request),
                code_verifier=pending["verifier"],
                nonce=pending["nonce"],
            )
        else:
            principal, tried = await provider.finish_trial_login(
                code=code,
                redirect_uri=_callback_url(request),
                code_verifier=pending["verifier"],
                nonce=pending["nonce"],
                trial=trial[1],
            )
    except IdentityError as error:
        await report(request, LOGIN_EVENT, error.code, actor=ANONYMOUS)
        return _message(request, locale, 401, "admin.login_failed.title", "admin.login_failed.text")
    principal = await role_service(request).attach(principal)
    start_session(request, principal, _clock(request).now())
    await service.record_login(principal)
    log.info("admin.login", actor=principal.subject, roles=role_keys(principal))
    if trial is not None and tried is not None:
        return await _finish_login_trial(request, principal, trial, tried)
    return RedirectResponse(safe_next(pending.get("next")), status_code=303)


async def _finish_login_trial(
    request: Request, principal: Principal, trial: tuple[str, dict[str, str]], tried: Principal
) -> Response:
    """Save the proposed login mapping if the dry run kept this superuser (ADR 0042 §8).

    The proposal belongs to the admin who made it: a login by anyone else counts as a failed
    dry run, never as theirs.
    """
    proposer, values = trial
    if proposer != principal.subject:
        tried = replace(tried, subject=proposer)
    service: SettingsService = request.app.state.settings_service
    try:
        await service.change_login_mapping(principal, values, trial=tried)
    except DomainError:
        return RedirectResponse("/admin/settings?done=login_mapping_refused", status_code=303)
    return RedirectResponse("/admin/settings?done=login_mapping_saved", status_code=303)


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
    roles = role_labels(principal, locale)
    return _page(request, locale, "admin/rights.html", principal, {"rights": rows, "roles": roles})


@dataclass(frozen=True)
class MatrixRowView:
    """A translated matrix row."""

    label: str
    level: str
    granted: list[bool]
    locked: bool
    pii: bool


@dataclass(frozen=True)
class RoleChangeView:
    """One entry of the roles' change history (ADR 0039 item 7)."""

    when: str
    actor: str
    action: str
    role: str
    version: str
    detail: str


CHANGED_FIELD_LABELS: dict[str, str] = {
    "name": "admin.matrix.history.name",
    "claim_value": "admin.matrix.history.claim_value",
}

ROLE_CHANGE_LABELS: dict[str, str] = {
    ROLE_CREATED: "admin.matrix.history.created",
    ROLE_UPDATED: "admin.matrix.history.updated",
    ROLE_DELETED: "admin.matrix.history.deleted",
}


def _role_change(
    request: Request, locale: RequestLocale, entry: AuditEntry, names: dict[str, str]
) -> RoleChangeView:
    """Translate a role change: which role, which version, which permissions."""
    _ = locale.gettext
    parts: dict[str, str] = {}
    added: list[str] = []
    removed: list[str] = []
    detail: list[str] = []
    for part in (p for p in entry.event.detail.split(",") if p):
        name, _sep, value = part.partition(":")
        if name == "perm.add":
            added.append(
                _("admin.matrix.history.count", count=int(value)) if value.isdigit() else value
            )
        elif name == "perm.remove":
            removed.append(
                _("admin.matrix.history.count", count=int(value)) if value.isdigit() else value
            )
        elif part in CHANGED_FIELD_LABELS:
            detail.append(_(CHANGED_FIELD_LABELS[part]))
        else:
            parts[name] = value
    if removed:
        detail.insert(0, _("admin.matrix.history.removed", permissions=", ".join(removed)))
    if added:
        detail.insert(0, _("admin.matrix.history.added", permissions=", ".join(added)))
    key = entry.event.reason
    return RoleChangeView(
        when=admin_moment(request, locale, entry.event.at),
        actor=entry.actor_name or entry.event.actor,
        action=_(ROLE_CHANGE_LABELS.get(entry.event.action, "admin.matrix.history.updated")),
        role=names.get(key, key),
        version=parts.get("version", ""),
        detail="; ".join(detail),
    )


@router.get("/matrix", response_class=HTMLResponse)
async def matrix(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.MATRIX_VIEW))],
) -> HTMLResponse:
    """Show the matrix with its bounds, the custom roles and who changed them (ADR 0039)."""
    _ = locale.gettext
    service = role_service(request)
    custom = (await service.catalogue()).custom
    rows = [
        MatrixRowView(
            _(PERMISSION_LABELS[row.permission]),
            _(LEVEL_LABELS[row.level]),
            [row.granted[role] for role in Role] + [row.permission in c.granted for c in custom],
            row.locked,
            row.pii,
        )
        for row in matrix_rows()
    ]
    citizen = [
        (_(CITIZEN_LABELS[action.key]), _(CONDITION_LABELS[action.condition]))
        for action in CITIZEN_ACTIONS
    ]
    names = {c.key: c.name(locale.code) for c in custom}
    return _page(
        request,
        locale,
        "admin/matrix.html",
        context.principal,
        {
            "rows": rows,
            "role_columns": [_(ROLE_LABELS[r]) for r in Role]
            + [c.name(locale.code) for c in custom],
            "custom_count": len(custom),
            "history": [_role_change(request, locale, e, names) for e in await service.history()],
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

    async def maintenance_refused(request: Request, exc: Exception) -> Response:
        locale = resolve_locale(request)
        return _message(
            request, locale, 503, "admin.maintenance.refused.title", "error.maintenance"
        )

    async def reauth_required(request: Request, exc: Exception) -> Response:
        next_path = exc.next_path if isinstance(exc, ReauthRequired) else "/admin/"
        target = quote(safe_next(next_path), safe="/")
        return RedirectResponse(f"/admin/login?next={target}", status_code=303)

    handlers: dict[type[Exception], Callable[[Request, Exception], object]] = {
        LoginRequired: login_required,
        AccessDenied: access_denied,
        MaintenanceRefused: maintenance_refused,
        ReauthRequired: reauth_required,
    }
    for exc_type, handler in handlers.items():
        app.add_exception_handler(exc_type, handler)  # type: ignore[arg-type]
    # Custom roles (ADR 0039) are attached first; then the update banner (ADR 0029) and the
    # maintenance bar (ADR 0038) are loaded for every admin page in one place.
    banner = [
        Depends(load_principal),
        Depends(update_routes.load_update_notice),
        Depends(maintenance_routes.load_maintenance_notice),
    ]
    for module_router in (
        router,
        audit_routes.router,
        retention_routes.router,
        settings_routes.router,
        organisation_settings_routes.router,
        role_routes.router,
        staff_routes.router,
        group_routes.router,
        call_routes.router,
        agenda_routes.router,
        planner_routes.router,
        queue_routes.router,
        check_routes.router,
        branding_routes.router,
        holiday_routes.router,
        channel_routes.router,
        message_routes.router,
        organisation_routes.router,
        closure_routes.router,
        closure_image_routes.router,
        scenario_routes.router,
        contact_routes.router,
        embedding_routes.router,
        form_routes.router,
        api_routes.router,
        simulator_routes.router,
        update_routes.router,
        maintenance_routes.router,
        help_routes.router,
    ):
        app.include_router(module_router, dependencies=banner)
