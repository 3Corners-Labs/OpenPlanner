"""The *Login* sub-tab of an organisation: its identity providers (ADR 0083 step 2).

The status is for everyone with ``identity_providers.view`` on the organisation (org admin,
auditor, superuser); every change needs ``identity_providers.manage`` (locked to the
superuser) and a fresh login. Changes are POST forms with the Fetch Metadata check. The
client secret is write-only: its field is always empty and a blank field keeps the stored
one (ADR 0008). A private key is never shown; the page shows the key id and the JWKS
address the IdP team registers. ``IdentityProviderService`` authorises again, validates,
checks discovery and audits.

The guided test login (point 9) sends the superuser to the provider and shows what came back
on the callback (``routes.callback``) without starting a session.

    GET  /admin/organisations/{org}/login                          identity_providers.view
    GET  /admin/organisations/{org}/login/new                      identity_providers.manage
    POST /admin/organisations/{org}/login                          identity_providers.manage
    GET  /admin/organisations/{org}/login/{idp_key}                identity_providers.view
    POST /admin/organisations/{org}/login/{idp_key}                identity_providers.manage
    POST /admin/organisations/{org}/login/{idp_key}/enable         identity_providers.manage
    POST /admin/organisations/{org}/login/{idp_key}/delete         identity_providers.manage
    POST /admin/organisations/{org}/login/{idp_key}/rotate-key     identity_providers.manage
    POST /admin/organisations/{org}/login/{idp_key}/test           identity_providers.manage
    GET  /admin/jwks.json                                          public (the JWK Set)
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import (
    Field,
    FormData,
    done_message,
    options,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import (
    Locale,
    SameOrigin,
    admin_moment,
    organisation_header,
    role_name,
    templates,
    with_manual,
)
from app.admin.pages import page as _page
from app.admin.session import save_login_start, save_login_test
from app.domain.authorization import Permission, Principal, Role
from app.domain.entitlements import Resolution
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_provider_service import IdentityProviderService, ProviderDetail
from app.domain.identity_providers import (
    DEFAULT_SCOPES,
    ClientAuth,
    OrganisationProvider,
    ProviderDraft,
)
from app.domain.ports import IdentityError, LoginTestReport
from app.web.locale import RequestLocale
from app.web.protection import limiter, read_limit
from app.web.templates import render

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.IDENTITY_PROVIDERS_VIEW, organisation_param="org")),
]
Manage = Annotated[
    AdminContext, Depends(RequirePermission(Permission.IDENTITY_PROVIDERS_MANAGE, fresh=True))
]

JWKS_PATH = "/admin/jwks.json"
"""Where the public keys of every provider are published (ADR 0083 point 6)."""

AUTH_LABELS: dict[ClientAuth, str] = {
    ClientAuth.PRIVATE_KEY_JWT: "admin.idp.auth.private_key_jwt",
    ClientAuth.CLIENT_SECRET_BASIC: "admin.idp.auth.client_secret_basic",
}
"""Label per client authentication method; a test checks every method has one."""

TEST_FAILURES: dict[str, str] = {
    "discovery_failed": "admin.idp.test_failed.discovery_failed",
    "token_exchange_failed": "admin.idp.test_failed.token_exchange_failed",
    "invalid_token": "admin.idp.test_failed.invalid_token",
    "nonce_mismatch": "admin.idp.test_failed.invalid_token",
    "no_signing_key": "admin.idp.test_failed.no_signing_key",
    "too_many_entitlements": "admin.idp.test_failed.too_many_entitlements",
}
"""Explanation per failure code of a test login; the guard's reasons are generic."""


def service(request: Request) -> IdentityProviderService:
    """Return the application's identity provider service."""
    found: IdentityProviderService = request.app.state.identity_provider_service
    return found


def _url(org: str, key: str | None = None) -> str:
    """Return the sub-tab, or the page of one provider."""
    base = f"/admin/organisations/{org}/login"
    return f"{base}/{key}" if key else base


def _jwks_url(request: Request) -> str:
    """Return the public JWKS address the IdP team registers."""
    return f"{request.app.state.settings.public_base_url.rstrip('/')}{JWKS_PATH}"


def _callback_url(request: Request) -> str:
    """Return the redirect URI registered at every provider (one for all, point 14)."""
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/admin/callback"


@dataclass(frozen=True)
class ProviderView:
    """A provider as the page shows it; never a secret or a private key."""

    provider: OrganisationProvider
    auth_label: str
    checked: str | None
    updated: str


def _view(request: Request, locale: RequestLocale, provider: OrganisationProvider) -> ProviderView:
    """Translate a provider for the list and the detail page."""
    return ProviderView(
        provider,
        locale.gettext(AUTH_LABELS[provider.client_auth]),
        admin_moment(request, locale, provider.checked_at) if provider.checked_at else None,
        admin_moment(request, locale, provider.updated_at),
    )


def _fields(
    request: Request,
    locale: RequestLocale,
    form: FormData,
    current: OrganisationProvider | None,
    defaults: tuple[str, str],
) -> list[Field]:
    """Return the inputs of the registration form, from the submission or the stored provider."""
    _ = locale.gettext
    claim, prefix = defaults

    def value(name: str, stored: str) -> str:
        return form.values.get(name, stored) if form.values else stored

    fields = [
        Field(
            "key",
            _("admin.idp.key"),
            value=value("key", current.key if current else ""),
            hint=_("admin.idp.key_hint"),
            readonly=current is not None,
            attributes={"maxlength": "63", "pattern": "[a-z0-9][a-z0-9-]{0,62}"},
        ),
        Field(
            "name",
            _("admin.idp.name"),
            value=value("name", current.name if current else ""),
            hint=_("admin.idp.name_hint"),
            attributes={"maxlength": "80"},
        ),
        Field(
            "discovery_url",
            _("admin.idp.discovery_url"),
            kind="url",
            value=value("discovery_url", current.discovery_url if current else ""),
            hint=_("admin.idp.discovery_url_hint"),
            attributes={"maxlength": "500", "autocomplete": "off"},
        ),
        Field(
            "client_id",
            _("admin.idp.client_id"),
            value=value("client_id", current.client_id if current else ""),
            hint=_("admin.idp.client_id_hint"),
            attributes={"maxlength": "255", "autocomplete": "off"},
        ),
        Field(
            "client_auth",
            _("admin.idp.client_auth"),
            kind="select",
            value=value(
                "client_auth",
                current.client_auth.value if current else ClientAuth.PRIVATE_KEY_JWT.value,
            ),
            hint=_("admin.idp.client_auth_hint"),
            options=options((m.value, _(AUTH_LABELS[m])) for m in ClientAuth),
        ),
        Field(
            "client_secret",
            _("admin.idp.client_secret"),
            kind="password",
            hint=_("admin.idp.client_secret_hint"),
            required=False,
            attributes={"maxlength": "512", "autocomplete": "new-password"},
        ),
        Field(
            "scopes",
            _("admin.idp.scopes"),
            value=value("scopes", current.scopes if current else DEFAULT_SCOPES),
            hint=_("admin.idp.scopes_hint"),
            attributes={"maxlength": "200"},
        ),
        Field(
            "entitlement_claim",
            _("admin.idp.entitlement_claim"),
            value=value("entitlement_claim", current.entitlement_claim if current else claim),
            hint=_("admin.idp.entitlement_claim_hint"),
            attributes={"maxlength": "64"},
        ),
        Field(
            "entitlement_prefix",
            _("admin.idp.entitlement_prefix"),
            value=value("entitlement_prefix", current.entitlement_prefix if current else prefix),
            hint=_("admin.idp.entitlement_prefix_hint"),
            attributes={"maxlength": "32"},
        ),
    ]
    filled = [
        Field(**{**f.__dict__, "error": form.error_for(f.name)}) if form.errors else f
        for f in fields
    ]
    return with_manual(request, locale, filled)


def _draft(form: FormData, key: str | None = None) -> ProviderDraft:
    """Build the draft from the form; the domain validates it."""
    return ProviderDraft.create(
        key=key if key is not None else form.text("key"),
        name=form.text("name"),
        discovery_url=form.text("discovery_url"),
        client_id=form.text("client_id"),
        client_auth=form.text("client_auth"),
        scopes=form.text("scopes"),
        entitlement_claim=form.text("entitlement_claim"),
        entitlement_prefix=form.text("entitlement_prefix"),
    )


def _secret(form: FormData) -> str | None:
    """Return the submitted secret, or ``None`` when the field was left blank."""
    return form.text("client_secret") or None


def _defaults(request: Request) -> tuple[str, str]:
    """Return the platform's entitlement claim and prefix as the form's defaults (point 5)."""
    settings = request.app.state.settings
    claim: str = settings.oidc_entitlement_claim
    prefix: str = settings.oidc_entitlement_prefix
    return claim, prefix


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    detail: ProviderDetail | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the registration form, alone (new) or under the provider's status (detail)."""
    organisation = (
        detail.organisation
        if detail
        else (await service(request).overview(context.principal, org)).organisation
    )
    current = detail.provider if detail else None
    response = _page(
        request,
        locale,
        "admin/organisation_login_form.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "detail": detail,
            "view": _view(request, locale, current) if current else None,
            "current_key": detail.current_key if detail else None,
            "retired_keys": [k for k in detail.keys if k.retired_at] if detail else [],
            "fields": _fields(request, locale, form, current, _defaults(request)),
            "errors": form.errors,
            "jwks_url": _jwks_url(request),
            "callback_url": _callback_url(request),
            "done": done_message(request, locale.gettext),
            "test_failed": _test_failure(request, locale),
        },
    )
    response.status_code = status
    return response


def _test_failure(request: Request, locale: RequestLocale) -> str | None:
    """Return the explanation of a failed test login named in the query, if any."""
    code = request.query_params.get("test_failed", "")
    if not code:
        return None
    return locale.gettext(TEST_FAILURES.get(code, "admin.idp.test_failed.generic"))


@router.get("/organisations/{org}/login", response_class=HTMLResponse)
async def organisation_login(
    request: Request, locale: Locale, org: str, context: View
) -> HTMLResponse:
    """Show the organisation's providers and their status."""
    overview = await service(request).overview(context.principal, org)
    organisation = overview.organisation
    return _page(
        request,
        locale,
        "admin/organisation_login.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "providers": [_view(request, locale, p) for p in overview.providers],
            "limit": overview.limit,
            "room": len(overview.providers) < overview.limit,
            "done": done_message(request, locale.gettext),
        },
    )


@router.get("/organisations/{org}/login/new", response_class=HTMLResponse)
async def new_provider(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the form for a new provider."""
    return await _form_page(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/login", response_class=HTMLResponse)
async def create_provider(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Register a provider, switched off, after the discovery check."""
    form = await read_form(request, locale)
    try:
        provider = await service(request).create(
            context.principal, org, _draft(form), _secret(form)
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, org, form, status=422)
    return redirect_done(_url(org, provider.key), "idp_created")


@router.get("/organisations/{org}/login/{idp_key}", response_class=HTMLResponse)
async def provider_page(
    request: Request, locale: Locale, org: str, idp_key: str, context: View
) -> Response:
    """Show one provider: status, keys, actions and the registration form."""
    detail = await service(request).provider(context.principal, org, idp_key)
    return await _form_page(request, locale, context, org, FormData({}, locale.gettext), detail)


@router.post("/organisations/{org}/login/{idp_key}", response_class=HTMLResponse)
async def update_provider(
    request: Request, locale: Locale, org: str, idp_key: str, _: SameOrigin, context: Manage
) -> Response:
    """Change a provider's registration."""
    detail = await service(request).provider(context.principal, org, idp_key)
    form = await read_form(request, locale)
    try:
        changed = await service(request).update(
            context.principal, org, idp_key, _draft(form, idp_key), _secret(form)
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, org, form, detail, 422)
    return redirect_done(_url(org, idp_key), "saved" if changed else "unchanged")


@router.post("/organisations/{org}/login/{idp_key}/enable", response_class=HTMLResponse)
async def enable_provider(
    request: Request, locale: Locale, org: str, idp_key: str, _: SameOrigin, context: Manage
) -> Response:
    """Switch the provider on (``enabled=on``) or off."""
    form = await read_form(request, locale)
    enabled = form.text("enabled") == "on"
    try:
        changed = await service(request).set_enabled(context.principal, org, idp_key, enabled)
    except DomainError as error:
        raise_refusal(error)
        raise
    if not changed:
        return redirect_done(_url(org, idp_key), "unchanged")
    return redirect_done(_url(org, idp_key), "idp_enabled" if enabled else "idp_disabled")


@router.post("/organisations/{org}/login/{idp_key}/delete", response_class=HTMLResponse)
async def delete_provider(
    request: Request, locale: Locale, org: str, idp_key: str, _: SameOrigin, context: Manage
) -> Response:
    """Remove the provider with its secret and keys."""
    try:
        await service(request).delete(context.principal, org, idp_key)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(_url(org), "removed")


@router.post("/organisations/{org}/login/{idp_key}/rotate-key", response_class=HTMLResponse)
async def rotate_key(
    request: Request, locale: Locale, org: str, idp_key: str, _: SameOrigin, context: Manage
) -> Response:
    """Generate a new signing key; the old one stays published for a day."""
    try:
        await service(request).rotate_key(context.principal, org, idp_key)
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.INVALID_CONFIGURATION:
            return redirect_done(_url(org, idp_key), "unchanged")
        raise
    return redirect_done(_url(org, idp_key), "idp_key_rotated")


@router.post("/organisations/{org}/login/{idp_key}/test", response_class=HTMLResponse)
async def start_test_login(
    request: Request, locale: Locale, org: str, idp_key: str, _: SameOrigin, context: Manage
) -> Response:
    """Send the superuser to the provider for a guided test login (point 9).

    The pending login is bound to the provider and the test to this admin; the callback
    shows the report and starts no session. A page that navigates on keeps CSP
    ``form-action 'self'`` strict, as the logout does.
    """
    detail = await service(request).provider(context.principal, org, idp_key)
    adapter = await request.app.state.organisation_providers.provider(detail.provider.key)
    if adapter is None:
        return redirect_done(_url(org, idp_key), "unchanged")
    try:
        start = await adapter.start_login(_callback_url(request), fresh=True)
    except IdentityError as error:
        await service(request).record_test_login(context.principal, org, idp_key, reason=error.code)
        return RedirectResponse(f"{_url(org, idp_key)}?test_failed={error.code}", status_code=303)
    now = request.app.state.clock.now()
    save_login_start(request, start, _url(org, idp_key), provider=idp_key)
    save_login_test(request, context.principal.subject, org, idp_key, now)
    return render(
        templates,
        request,
        "admin/idp_test_redirect.html",
        locale,
        {"login_url": start.url, "name": detail.provider.name},
    )


@dataclass(frozen=True, order=True)
class GrantView:
    """One grant the test login's entitlements resolve to, in the admin's words."""

    role: str
    target: str


def test_result_page(
    request: Request,
    locale: RequestLocale,
    context_principal: Principal,
    org: str,
    key: str,
    report: LoginTestReport,
    resolution: Resolution,
) -> HTMLResponse:
    """Render what a test login showed (called by the callback route).

    Names and verdicts only: the claim names, whether the address was verified, its domain,
    the grants the entitlement values resolve to and the values that were refused.
    """
    principal = resolution.principal
    custom = {f"custom.{r.id}": r.name(locale.code) for r in principal.custom_roles}
    grants = sorted(
        GrantView(_role_label(g.role, locale, custom), _target(g.organisation, g.group))
        for g in principal.grants
    )
    return _page(
        request,
        locale,
        "admin/idp_test_result.html",
        context_principal,
        {
            "org": org,
            "key": key,
            "report": report,
            "grants": grants,
            "refused": [(r.value, r.reason.value) for r in resolution.refused],
            "back_url": _url(org, key),
        },
    )


def _role_label(role: str, locale: RequestLocale, custom: dict[str, str]) -> str:
    """Return a grant's role name: translated when built in, its own name when custom."""
    try:
        return role_name(Role(role), locale)
    except ValueError:
        return custom.get(role, role)


def _target(organisation: str | None, group: str | None) -> str:
    """Return the target of a grant as ``org`` or ``org/group``; empty for the platform."""
    if organisation is None:
        return ""
    return f"{organisation}/{group}" if group else organisation


@router.get(JWKS_PATH.removeprefix("/admin"), include_in_schema=False)
@limiter.limit(read_limit)
async def jwks(request: Request) -> JSONResponse:
    """Publish the public keys of every provider (one JWK Set, ``kid`` per key).

    Public and rate-limited like the other reads without a login (CLAUDE.md security
    baseline); the document holds public keys only.
    """
    document = await service(request).published_keys()
    return JSONResponse(document, headers={"cache-control": "public, max-age=300"})
