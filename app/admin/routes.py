"""Admin portal routes (WP7a): login, overview, own rights, matrix, integrations.

Every route except the login flow depends on exactly one ``RequirePermission``; the public
ones are listed in ``PUBLIC_ADMIN_PATHS`` and the route-coverage test checks both.

    GET  /admin/login        → identity provider (state, nonce, PKCE in the session)
    GET  /admin/callback     → verify, start a fresh session, audit the login
    POST /admin/logout       → end the session here and at the provider, audit the logout
    GET  /admin/rights       rights.view      own permissions with their scope
    GET  /admin/matrix       matrix.view      the whole matrix (same data as the docs)

The overview (``/admin/``, ADR 0061) lives in ``overview_routes``, the audit log in
``audit_routes``, the retention overview in ``retention_routes``, the staff directory in
``staff_routes``, System status and Advanced settings in ``settings_routes``, groups and their
planning in ``group_routes``, organisations in ``organisation_routes`` and their branding in
``branding_routes``.
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
from app.adapters.service_app_check import SERVICE_APP_REPORT
from app.admin import (
    agenda_routes,
    alerts,
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
    group_list_routes,
    group_routes,
    help_routes,
    holiday_routes,
    idp_mapping_routes,
    idp_routes,
    logging_routes,
    maintenance_routes,
    message_routes,
    organisation_routes,
    organisation_settings_routes,
    organisation_text_routes,
    overview_routes,
    planner_routes,
    preference_routes,
    queue_routes,
    retention_routes,
    role_routes,
    scenario_routes,
    session_routes,
    settings_routes,
    simulator_routes,
    staff_directory_routes,
    staff_routes,
    staff_status_routes,
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
    login_service,
    role_service,
    session_policy,
    session_principal,
    session_service,
)
from app.admin.forms import Field, read_form, safe_next
from app.admin.idp_mapping_routes import reason_label
from app.admin.idp_routes import JWKS_PATH
from app.admin.integration_views import check_view
from app.admin.pages import (
    Locale,
    SameOrigin,
    admin_moment,
    role_labels,
    role_name,
    scope_text,
    templates,
)
from app.admin.pages import message as _message
from app.admin.pages import page as _page
from app.admin.session import (
    LOGOUT_CLEAR_SITE_DATA,
    current_session,
    end_session,
    has_login_trial,
    mark_logged_out,
    pop_logged_out,
    pop_login_start,
    pop_login_test,
    pop_login_trial,
    save_login_start,
    session_theme,
    start_session,
)
from app.admin.top_bar import load_top_counts
from app.domain.admin import ANONYMOUS, LOGIN_EVENT
from app.domain.audit import AuditEntry
from app.domain.authorization import (
    API_SCOPES,
    PARTICIPANT_ACTIONS,
    Permission,
    Principal,
    Role,
    permissions_for,
    role_keys,
)
from app.domain.entitlements import ClaimMode, LoginClaims, resolve
from app.domain.errors import DomainError
from app.domain.identity import PLATFORM
from app.domain.login_domains import EMAIL_DOMAIN_MISMATCH, EMAIL_NOT_VERIFIED
from app.domain.login_service import (
    AMBIGUOUS_CLAIMS,
    SUSPENDED,
    TOO_MANY_ENTITLEMENTS,
    LoginRefusedError,
)
from app.domain.mfa import MFA_MISSING_EVENT, MFA_REQUIRED
from app.domain.ports import Clock, IdentityError, IdentityProvider
from app.domain.roles import ROLE_CREATED, ROLE_DELETED, ROLE_UPDATED
from app.domain.settings_service import SettingsService
from app.web.authz_labels import (
    API_SCOPE_LABELS,
    CONDITION_LABELS,
    LEVEL_LABELS,
    PARTICIPANT_LABELS,
    PERMISSION_LABELS,
)
from app.web.authz_report import matrix_rows
from app.web.errors import FormError
from app.web.locale import RequestLocale, resolve_locale
from app.web.protection import limiter, read_limit
from app.web.security_events import report
from app.web.templates import render

router = APIRouter(prefix="/admin", include_in_schema=False)
log = structlog.get_logger()

LOGIN_REFUSED_TEXTS: dict[str, str] = {
    AMBIGUOUS_CLAIMS: "admin.login_refused.ambiguous_claims",
    TOO_MANY_ENTITLEMENTS: "admin.login_refused.too_many_entitlements",
    MFA_REQUIRED: "admin.login_refused.mfa_required",
    SUSPENDED: "admin.login_refused.suspended",
    # ADR 0083 points 14 and 15: an organisation provider's login that does not hold up.
    EMAIL_NOT_VERIFIED: "admin.login_refused.email_not_verified",
    EMAIL_DOMAIN_MISMATCH: "admin.login_refused.email_domain_mismatch",
    "issuer_mismatch": "admin.login_refused.issuer_mismatch",
}
"""What the error page says per refusal reason of the login service (ADR 0049)."""

PUBLIC_ADMIN_PATHS = frozenset(
    {"/admin/login", "/admin/callback", "/admin/logout", "/admin/logged-out", JWKS_PATH}
)
"""The only admin paths without a ``RequirePermission``: the login flow itself, and the
public key set of the organisation providers (ADR 0083 point 6)."""


def _identity_provider(request: Request) -> IdentityProvider | None:
    """Return the configured identity provider, or ``None`` when admin login is disabled."""
    provider: IdentityProvider | None = request.app.state.identity_provider
    return provider


async def _provider_for(
    request: Request, key: str, test: tuple[str, str, str] | None
) -> IdentityProvider | None:
    """Return the provider a pending login started at, or ``None`` if it is not known.

    The platform provider completes logins. An organisation provider completes the guided
    test login that started at it (ADR 0083 step 2), and a login routed to it while it is
    switched on and the host allows organisation providers (step 3). Any other key is refused
    like a forged ``state``.
    """
    if key == PLATFORM:
        return _identity_provider(request)
    if test is None and await idp_routes.service(request).live_provider(key) is None:
        return None
    if test is not None and test[2] != key:
        return None
    found: IdentityProvider | None = await request.app.state.organisation_providers.provider(key)
    return found


def _clock(request: Request) -> Clock:
    """Return the application's clock."""
    clock: Clock = request.app.state.clock
    return clock


def _callback_url(request: Request) -> str:
    """Return the redirect URI registered at the identity provider."""
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/admin/callback"


IDENTIFIER_MAX_LENGTH = 254
"""The longest e-mail address (RFC 5321 path limit); a longer identifier is refused."""


def _identifier_page(
    request: Request,
    locale: Locale,
    next_url: str,
    value: str = "",
    error: str | None = None,
) -> HTMLResponse:
    """Render the e-mail-first login page (ADR 0083 point 13, ADR 0106 point 9)."""
    _ = locale.gettext
    identifier = Field(
        "identifier",
        _("admin.login.identifier.label"),
        value=value,
        hint=_("admin.login.identifier.hint"),
        error=error,
        attributes={
            "autocomplete": "username",
            "spellcheck": "false",
            "autocapitalize": "none",
            "maxlength": str(IDENTIFIER_MAX_LENGTH),
        },
    )
    errors = [FormError("identifier", error)] if error else []
    response = render(
        templates,
        request,
        "admin/login.html",
        locale,
        {
            "identifier": identifier,
            "errors": errors,
            "next_url": next_url,
            "theme": session_theme(request),
        },
    )
    response.status_code = 422 if error else 200
    return response


@router.get("/login", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def login(
    request: Request,
    locale: Locale,
    next: str | None = None,  # noqa: A002
    fresh: bool = False,
    platform: bool = False,
) -> Response:
    """Send the admin to the identity provider; ``fresh`` for a mapping dry run (§8).

    When organisation providers route logins (ADR 0083), the page asks for the address first;
    ``platform`` skips that question and goes to the platform provider.
    """
    provider = _identity_provider(request)
    if provider is None:
        return _message(
            request, locale, 503, "admin.not_configured.title", "admin.not_configured.text"
        )
    own = await _session_provider(request)
    if own is not None:
        # ADR 0083 point 17: a fresh login of a session goes to the provider it came from.
        key, provider = own
    elif not fresh and not platform and await idp_routes.service(request).offers_choice():
        return _identifier_page(request, locale, safe_next(next))
    else:
        key = PLATFORM
    try:
        start = await provider.start_login(
            _callback_url(request), fresh=fresh and has_login_trial(request)
        )
    except IdentityError:
        return _message(request, locale, 503, "admin.login_failed.title", "admin.login_failed.text")
    save_login_start(request, start, safe_next(next), key)
    return RedirectResponse(start.url, status_code=303)


async def _session_provider(request: Request) -> tuple[str, IdentityProvider] | None:
    """Return the organisation provider of the live session, if it came from one.

    A fresh login (ADR 0015) and the logout go to the session's own provider (ADR 0083 point
    17), never to the e-mail-first question, so a re-authentication cannot switch identity. A
    session of the platform provider, no session, or a provider that is no longer live gives
    ``None``: the ordinary login then applies.
    """
    found = current_session(request)
    if found is None or found.provider == PLATFORM:
        return None
    if await idp_routes.service(request).live_provider(found.provider) is None:
        return None
    adapter: IdentityProvider | None = await request.app.state.organisation_providers.provider(
        found.provider
    )
    return (found.provider, adapter) if adapter is not None else None


@router.post("/login", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def login_identifier(request: Request, locale: Locale, _: SameOrigin) -> Response:
    """Send the admin to the provider their address belongs to (ADR 0083 point 13).

    Only the domain of the address counts; the address is neither stored nor logged (B12). It
    goes to the chosen provider as ``login_hint``. The reserved suffix of the bundled provider
    and every address without a routing domain go to the platform provider.
    """
    form = await read_form(request, locale)
    identifier = form.text("identifier").strip()
    next_url = safe_next(form.text("next"))
    if not identifier or len(identifier) > IDENTIFIER_MAX_LENGTH:
        error = locale.gettext("admin.login.identifier.error")
        return _identifier_page(
            request, locale, next_url, identifier[:IDENTIFIER_MAX_LENGTH], error
        )
    platform = _identity_provider(request)
    organisation = await idp_routes.service(request).login_provider(identifier)
    key = organisation.key if organisation is not None else PLATFORM
    provider = (
        await request.app.state.organisation_providers.provider(key)
        if organisation is not None
        else platform
    )
    if provider is None:
        return _message(
            request, locale, 503, "admin.not_configured.title", "admin.not_configured.text"
        )
    try:
        start = await provider.start_login(_callback_url(request), login_hint=identifier)
    except IdentityError:
        return _message(request, locale, 503, "admin.login_failed.title", "admin.login_failed.text")
    save_login_start(request, start, next_url, key)
    # Not a 303: browsers apply the CSP form-action to the redirect of a form post, so a redirect
    # to the provider would be blocked. The page moves on by itself, as the logout does.
    return render(templates, request, "admin/login_redirect.html", locale, {"login_url": start.url})


@router.get("/callback", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def callback(
    request: Request, locale: Locale, code: str | None = None, state: str | None = None
) -> Response:
    """Complete the login: check state, verify the token, start a fresh session."""
    pending = pop_login_start(request)
    now = _clock(request).now()
    max_age = timedelta(minutes=request.app.state.settings.admin_reauth_minutes)
    test = pop_login_test(request, now, max_age)
    key = pending.get("provider", PLATFORM) if pending else PLATFORM
    provider = await _provider_for(request, key, test)
    trial = pop_login_trial(request, now, max_age)
    if (
        provider is None
        or pending is None
        or not code
        or not state
        or not hmac.compare_digest(state, pending.get("state", ""))
    ):
        await report(request, LOGIN_EVENT, "invalid_state", actor=ANONYMOUS)
        return _message(request, locale, 400, "admin.login_failed.title", "admin.login_failed.text")
    if test is not None and key != PLATFORM:
        return await _finish_test_login(request, locale, provider, pending, test, code)
    if key != PLATFORM:
        return await _finish_organisation_login(request, locale, provider, pending, key, code)
    tried: LoginClaims | None = None
    try:
        if trial is None:
            claims = await provider.finish_login(
                code=code,
                redirect_uri=_callback_url(request),
                code_verifier=pending["verifier"],
                nonce=pending["nonce"],
            )
        else:
            claims, tried = await provider.finish_trial_login(
                code=code,
                redirect_uri=_callback_url(request),
                code_verifier=pending["verifier"],
                nonce=pending["nonce"],
                trial=trial[1],
            )
    except IdentityError as error:
        await report(request, LOGIN_EVENT, error.code, actor=ANONYMOUS)
        text = LOGIN_REFUSED_TEXTS.get(error.code, "admin.login_failed.text")
        return _message(request, locale, 401, "admin.login_failed.title", text)
    if claims.mfa_missing:  # ADR 0100: the provider's policy is "record"; the login goes on
        await report(
            request,
            MFA_MISSING_EVENT,
            "mfa_missing",
            detail=f"provider:{key}",
            actor=claims.subject,
        )
    logins = login_service(request)
    try:
        outcome = await logins.complete_login(claims)
    except LoginRefusedError as refused:
        # ADR 0049 points 6 and 14: audited by the service; the page names the reason.
        await report(request, LOGIN_EVENT, refused.reason, actor=claims.subject)
        text = LOGIN_REFUSED_TEXTS.get(refused.reason, "admin.login_failed.text")
        return _message(request, locale, 403, "admin.login_failed.title", text)
    principal = outcome.principal
    await start_session(
        request, session_service(request), outcome.session_claims, key, session_policy(request)
    )
    log.info("admin.login", actor=principal.subject, roles=role_keys(principal))
    if trial is not None and tried is not None:
        prefix = trial[1].get("oidc_entitlement_prefix", "")
        tried_principal = (await logins.preview(tried, prefix)).principal
        return await _finish_login_trial(request, principal, trial, tried_principal)
    return RedirectResponse(safe_next(pending.get("next")), status_code=303)


async def _finish_organisation_login(  # noqa: PLR0913
    request: Request,
    locale: Locale,
    provider: IdentityProvider,
    pending: dict[str, str],
    key: str,
    code: str,
) -> Response:
    """Complete a login routed to organisation provider ``key`` (ADR 0083 points 14 to 17).

    The answer must name the provider's issuer (RFC 9207), the ID token must carry a
    verified address on one of the provider's own routing domains, and the entitlements
    resolve only inside its organisation. The session records the provider.
    """
    providers = idp_routes.service(request)
    registered = await providers.live_provider(key)
    try:
        if registered is None:
            raise IdentityError("provider_unavailable")
        found = await provider.finish_organisation_login(
            code=code,
            redirect_uri=_callback_url(request),
            code_verifier=pending["verifier"],
            nonce=pending["nonce"],
            response_issuer=request.query_params.get("iss"),
        )
        refusal = await providers.identity_refusal(key, found.email, found.email_verified)
        if refusal is not None:
            raise IdentityError(refusal)
    except IdentityError as error:
        await report(request, LOGIN_EVENT, error.code, detail=f"provider:{key}", actor=ANONYMOUS)
        text = LOGIN_REFUSED_TEXTS.get(error.code, "admin.login_failed.text")
        return _message(request, locale, 401, "admin.login_failed.title", text)
    claims = found.claims
    if claims.mfa_missing:
        await report(
            request,
            MFA_MISSING_EVENT,
            "mfa_missing",
            detail=f"provider:{key}",
            actor=claims.subject,
        )
    try:
        outcome = await login_service(request).complete_login(claims, registered)
    except LoginRefusedError as refused:
        await report(request, LOGIN_EVENT, refused.reason, actor=claims.subject)
        text = LOGIN_REFUSED_TEXTS.get(refused.reason, "admin.login_failed.text")
        return _message(request, locale, 403, "admin.login_failed.title", text)
    await start_session(
        request, session_service(request), outcome.session_claims, key, session_policy(request)
    )
    log.info("admin.login", actor=outcome.principal.subject, roles=role_keys(outcome.principal))
    return RedirectResponse(safe_next(pending.get("next")), status_code=303)


async def _finish_test_login(
    request: Request,
    locale: Locale,
    provider: IdentityProvider,
    pending: dict[str, str],
    test: tuple[str, str, str],
    code: str,
) -> Response:
    """Show what a guided test login brought back, without a session (ADR 0083 point 9).

    The test belongs to the superuser who started it: it is completed only in that admin's
    own session, and audited as ``idp.test_login`` with the outcome.
    """
    subject, organisation, key = test
    principal = session_principal(request)
    if principal is None or principal.subject != subject:
        await report(request, LOGIN_EVENT, "test_login_mismatch", actor=ANONYMOUS)
        return _message(request, locale, 403, "admin.login_failed.title", "admin.login_failed.text")
    providers = idp_routes.service(request)
    back = f"/admin/organisations/{organisation}/login/{key}"
    try:
        found = await provider.finish_test_login(
            code=code,
            redirect_uri=_callback_url(request),
            code_verifier=pending["verifier"],
            nonce=pending["nonce"],
        )
    except IdentityError as error:
        await providers.record_test_login(principal, organisation, key, reason=error.code)
        return RedirectResponse(f"{back}?test_failed={error.code}", status_code=303)
    # ADR 0083 point 2: an organisation provider resolves in entitlements mode only.
    detail = await providers.provider(principal, organisation, key)
    directory, catalogue = await login_service(request).directory()
    resolution = resolve(
        found.claims,
        ClaimMode.ENTITLEMENTS,
        detail.provider.entitlement_prefix,
        directory,
        catalogue,
    )
    await providers.record_test_login(principal, organisation, key)
    return idp_routes.test_result_page(
        request, locale, principal, organisation, key, found, resolution
    )


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
        return RedirectResponse(
            "/admin/settings/values?done=login_mapping_refused", status_code=303
        )
    return RedirectResponse("/admin/settings/values?done=login_mapping_saved", status_code=303)


@router.post("/logout", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def logout(request: Request, locale: Locale, _: SameOrigin) -> Response:
    """End the admin session here and at the identity provider (no silent re-login)."""
    principal = session_principal(request)
    own = await _session_provider(request)  # read before the session ends (ADR 0083 point 17)
    await end_session(request, session_service(request))
    if principal is not None:
        await admin_service(request).record_logout(principal)
    logged_out = f"{request.app.state.settings.public_base_url.rstrip('/')}/admin/logged-out"
    provider = own[1] if own is not None else _identity_provider(request)
    provider_logout = await provider.logout_url(logged_out) if provider else None
    mark_logged_out(request)
    if provider_logout is None:
        return RedirectResponse("/admin/logged-out", status_code=303)
    # A redirect from a form POST to the provider would be blocked by CSP form-action 'self';
    # a page that navigates on (meta refresh, plus a visible link) keeps the CSP strict.
    return render(
        templates, request, "admin/logout_redirect.html", locale, {"logout_url": provider_logout}
    )


@router.get("/logged-out", response_class=HTMLResponse)
async def logged_out(request: Request, locale: Locale) -> HTMLResponse:
    """Confirm the logout; after a logout of this browser, also clear the site's data (V14.3.1)."""
    cleared = pop_logged_out(request)
    response = _message(request, locale, 200, "admin.logged_out.title", "admin.logged_out.text")
    if cleared:
        response.headers.update(LOGOUT_CLEAR_SITE_DATA)
    return response


@router.get("/rights", response_class=HTMLResponse)
async def rights(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))],
) -> HTMLResponse:
    """Show the admin's own grants, permissions and where they apply, and refused values."""
    _ = locale.gettext
    principal = context.principal
    rows = [
        (_(PERMISSION_LABELS[permission]), scope_text(scope, _))
        for permission, scope in permissions_for(principal)
    ]
    roles = role_labels(principal, locale, with_standard=True)
    grants = sorted(g.key for g in principal.grants)
    refused = [
        (r.value, reason_label(r.reason, locale), admin_moment(request, locale, r.received_at))
        for r in await admin_service(request).own_refusals(principal)
    ]
    return _page(
        request,
        locale,
        "admin/rights.html",
        principal,
        {"rights": rows, "roles": roles, "grants": grants, "refused": refused},
    )


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
    participant = [
        (_(PARTICIPANT_LABELS[action.key]), _(CONDITION_LABELS[action.condition]))
        for action in PARTICIPANT_ACTIONS
    ]
    names = {c.key: c.name(locale.code) for c in custom}
    return _page(
        request,
        locale,
        "admin/matrix.html",
        context.principal,
        {
            "rows": rows,
            "role_columns": [role_name(r, locale, with_standard=True) for r in Role]
            + [c.name(locale.code) for c in custom],
            "custom_count": len(custom),
            "history": [_role_change(request, locale, e, names) for e in await service.history()],
            "participant": participant,
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
        await reports.get(SERVICE_APP_REPORT),
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
            # ADR 0049 point 8: values were sent but none recognised; name no value.
            refused = await admin_service(request).own_refusals(principal)
            text = "admin.rights_not_recognised.text" if refused else "admin.no_role.text"
            return _message(request, locale, 403, "admin.forbidden.title", text, back_link=False)
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
    # Custom roles (ADR 0039) are attached first; then the update banner (ADR 0029), the
    # maintenance bar (ADR 0038), the counts of the top bar (SH-2) and the alert banner
    # (ADR 0061 §7) are loaded for every admin page in one place.
    banner = [
        Depends(load_principal),
        Depends(update_routes.load_update_notice),
        Depends(maintenance_routes.load_maintenance_notice),
        Depends(load_top_counts),
        Depends(alerts.load_alerts),
    ]
    for module_router in (
        router,
        overview_routes.router,
        group_list_routes.router,
        audit_routes.router,
        retention_routes.router,
        settings_routes.router,
        organisation_settings_routes.router,
        role_routes.router,
        staff_directory_routes.router,
        staff_routes.router,
        session_routes.router,
        staff_status_routes.router,
        group_routes.router,
        call_routes.router,
        agenda_routes.router,
        planner_routes.router,
        queue_routes.router,
        check_routes.router,
        branding_routes.router,
        holiday_routes.router,
        idp_mapping_routes.router,
        idp_routes.router,
        logging_routes.router,
        channel_routes.router,
        message_routes.router,
        organisation_routes.router,
        organisation_text_routes.router,
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
        preference_routes.router,
        alerts.router,
    ):
        app.include_router(module_router, dependencies=banner)
