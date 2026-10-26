"""Admin routes for the machine API (ADR 0023): switches and API clients, superuser only.

Every route depends on exactly one ``RequirePermission(api_clients.manage, fresh=True)``.
Changes are POST forms with the Fetch Metadata check. A new or rotated secret is shown once,
in the response to the POST itself (never in a URL, never cached); afterwards only its expiry
is visible. ``ApiAccessService`` authorises again, validates and audits.

    GET  /admin/api                            switches and clients
    POST /admin/api/settings                   API on/off, documentation on/off
    GET  /admin/api/clients/new                form for a new client
    POST /admin/api/clients                    register → secret page
    GET  /admin/api/clients/{client_id}        details, grant form and actions
    POST /admin/api/clients/{client_id}        change name and grant (revokes tokens)
    POST /admin/api/clients/{client_id}/rotate new secret → secret page (revokes tokens)
    POST /admin/api/clients/{client_id}/enable switch the client on or off
    POST /admin/api/clients/{client_id}/revoke revoke every live token
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import (
    FormData,
    done_message,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.config import AGENT_API_PREFIX as PREFIX
from app.domain.api_access import ApiAccessService
from app.domain.api_clients import (
    ACCESS_TOKEN_LIFETIME,
    SECRET_LIFETIME_DAYS,
    ApiClient,
    ClientDraft,
    IssuedSecret,
)
from app.domain.authorization import API_SCOPES, Permission
from app.domain.errors import DomainError
from app.domain.scheduling import GroupSummary
from app.web.authz_labels import API_SCOPE_LABELS
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext, Depends(RequirePermission(Permission.API_CLIENTS_MANAGE, fresh=True))
]
"""The permission of every page and change of the machine API."""

DEFAULT_SECRET_DAYS = 180
SCOPE_FIELD = "scope."
GROUP_FIELD = "group."


def api_access(request: Request) -> ApiAccessService:
    """Return the application's machine API service."""
    service: ApiAccessService = request.app.state.api_access
    return service


@dataclass(frozen=True)
class Choice:
    """One checkbox of a fieldset."""

    name: str
    label: str
    checked: bool
    hint: str = ""


@dataclass(frozen=True)
class ClientView:
    """An API client as the portal shows it; never the secret or a token."""

    client: ApiClient
    organisation_name: str
    scopes: str
    groups: str
    secret_expires: str
    secret_expiring: bool
    last_token: str | None


def _view(
    request: Request, locale: RequestLocale, client: ApiClient, names: dict[str, str]
) -> ClientView:
    """Translate a client for the overview and detail pages."""
    _ = locale.gettext
    now = request.app.state.clock.now()
    return ClientView(
        client=client,
        organisation_name=names.get(client.organisation, client.organisation),
        scopes=", ".join(_(API_SCOPE_LABELS[s]) for s in sorted(client.scopes)),
        groups=", ".join(sorted(client.groups)) or _("admin.api.all_groups"),
        secret_expires=admin_moment(request, locale, client.secret_expires_at),
        secret_expiring=client.secret_expiring(now),
        last_token=admin_moment(request, locale, client.last_token_at)
        if client.last_token_at
        else None,
    )


def _scope_choices(
    locale: RequestLocale, form: FormData, current: ApiClient | None
) -> list[Choice]:
    """Return a checkbox per API scope, ticked from the form or the stored client."""
    _ = locale.gettext
    return [
        Choice(
            f"{SCOPE_FIELD}{scope}",
            _(API_SCOPE_LABELS[scope]),
            (f"{SCOPE_FIELD}{scope}" in form.values)
            if form.values
            else bool(current and scope in current.scopes),
            str(scope),
        )
        for scope in API_SCOPES
    ]


def _group_choices(
    groups: list[GroupSummary], form: FormData, current: ApiClient | None
) -> list[Choice]:
    """Return a checkbox per group, labelled with its organisation."""
    return [
        Choice(
            f"{GROUP_FIELD}{g.group.slug}",
            f"{g.organisation_name} · {g.group.name}",
            (f"{GROUP_FIELD}{g.group.slug}" in form.values)
            if form.values
            else bool(current and g.group.slug in current.groups),
            g.group.slug,
        )
        for g in groups
        if current is None or g.organisation_slug == current.organisation
    ]


def _draft(form: FormData, organisation: str) -> ClientDraft:
    """Build the draft from the submitted form (the domain validates it)."""
    return ClientDraft.create(
        name=form.text("name"),
        organisation=organisation,
        groups=[k[len(GROUP_FIELD) :] for k in form.values if k.startswith(GROUP_FIELD)],
        scopes=[k[len(SCOPE_FIELD) :] for k in form.values if k.startswith(SCOPE_FIELD)],
        networks=form.text("networks").splitlines(),
    )


def _lifetime_days(form: FormData) -> int:
    """Return the chosen secret lifetime; an unknown choice is refused by the domain."""
    raw = form.text("lifetime_days")
    return int(raw) if raw.isdigit() and len(raw) <= 4 else 0


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData,
    current: ApiClient | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the new-client form, or the detail page of ``current`` with its grant form."""
    overview = await api_access(request).overview(context.principal)
    names = {o.slug: o.name for o in overview.organisations}
    response = _page(
        request,
        locale,
        "admin/api_client.html",
        context.principal,
        {
            "client": _view(request, locale, current, names) if current else None,
            "organisations": overview.organisations,
            "organisation": form.values.get("organisation", ""),
            "name": form.values.get("name", current.name if current else ""),
            "networks": form.values.get("networks", "\n".join(current.networks) if current else ""),
            "scopes": _scope_choices(locale, form, current),
            "groups": _group_choices(overview.groups, form, current),
            "lifetime_days": SECRET_LIFETIME_DAYS,
            "default_days": DEFAULT_SECRET_DAYS,
            "errors": form.errors,
            "error_for": form.error_for,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


def _secret_page(
    request: Request, locale: RequestLocale, context: AdminContext, issued: IssuedSecret
) -> HTMLResponse:
    """Show a new secret once; the response is never cached (``render`` sets no-store)."""
    return _page(
        request,
        locale,
        "admin/api_secret.html",
        context.principal,
        {
            "client": issued.client,
            "secret": issued.secret,
            "expires": admin_moment(request, locale, issued.client.secret_expires_at),
            "token_url": f"{request.app.state.settings.public_base_url.rstrip('/')}"
            f"{PREFIX}/oauth/token",
        },
    )


def _url(client_id: str | None = None) -> str:
    """Return the overview, or the detail page of one client."""
    return f"/admin/api/clients/{client_id}" if client_id else "/admin/api"


@router.get("/api", response_class=HTMLResponse)
async def overview(request: Request, locale: Locale, context: Manage) -> HTMLResponse:
    """Show the switches and every registered client."""
    found = await api_access(request).overview(context.principal)
    names = {o.slug: o.name for o in found.organisations}
    base = request.app.state.settings.public_base_url.rstrip("/")
    return _page(
        request,
        locale,
        "admin/api.html",
        context.principal,
        {
            "overview": found,
            "clients": [_view(request, locale, c, names) for c in found.clients],
            "docs_url": f"{base}{PREFIX}/docs",
            "token_minutes": int(ACCESS_TOKEN_LIFETIME.total_seconds() // 60),
            "done": done_message(request, locale.gettext),
        },
    )


@router.post("/api/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request, locale: Locale, _: SameOrigin, context: Manage
) -> Response:
    """Switch the API and its documentation on or off."""
    form = await read_form(request, locale)
    changed = await api_access(request).save_settings(
        context.principal,
        enabled=form.text("enabled") == "on",
        docs_public=form.text("docs_public") == "on",
    )
    return redirect_done(_url(), "saved" if changed else "unchanged")


@router.get("/api/clients/new", response_class=HTMLResponse)
async def new_client(request: Request, locale: Locale, context: Manage) -> Response:
    """Show the form for a new client."""
    return await _form_page(request, locale, context, FormData({}, locale.gettext))


@router.post("/api/clients", response_class=HTMLResponse)
async def create_client(
    request: Request, locale: Locale, _: SameOrigin, context: Manage
) -> Response:
    """Register a client and show its secret once."""
    form = await read_form(request, locale)
    try:
        draft = _draft(form, form.text("organisation"))
        issued = await api_access(request).create_client(
            context.principal, draft, _lifetime_days(form)
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, form, status=422)
    return _secret_page(request, locale, context, issued)


@router.get("/api/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(
    request: Request, locale: Locale, client_id: str, context: Manage
) -> Response:
    """Show one client with its grant form and actions."""
    current = await api_access(request).client(context.principal, client_id)
    return await _form_page(request, locale, context, FormData({}, locale.gettext), current)


@router.post("/api/clients/{client_id}", response_class=HTMLResponse)
async def update_client(
    request: Request, locale: Locale, client_id: str, _: SameOrigin, context: Manage
) -> Response:
    """Change name and grant; a changed grant revokes the client's tokens."""
    service = api_access(request)
    current = await service.client(context.principal, client_id)
    form = await read_form(request, locale)
    try:
        changed = await service.update_client(
            context.principal, client_id, _draft(form, current.organisation)
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, form, current, 422)
    return redirect_done(_url(client_id), "saved" if changed else "unchanged")


@router.post("/api/clients/{client_id}/rotate", response_class=HTMLResponse)
async def rotate_secret(
    request: Request, locale: Locale, client_id: str, _: SameOrigin, context: Manage
) -> Response:
    """Replace the secret and show the new one once."""
    form = await read_form(request, locale)
    service = api_access(request)
    try:
        issued = await service.rotate_secret(context.principal, client_id, _lifetime_days(form))
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        current = await service.client(context.principal, client_id)
        return await _form_page(request, locale, context, form, current, 422)
    return _secret_page(request, locale, context, issued)


@router.post("/api/clients/{client_id}/enable", response_class=HTMLResponse)
async def set_enabled(
    request: Request, locale: Locale, client_id: str, _: SameOrigin, context: Manage
) -> Response:
    """Switch a client on or off; off revokes its tokens at once."""
    form = await read_form(request, locale)
    enabled = form.text("enabled") == "on"
    await api_access(request).set_enabled(context.principal, client_id, enabled)
    return redirect_done(_url(client_id), "client_enabled" if enabled else "client_disabled")


@router.post("/api/clients/{client_id}/revoke", response_class=HTMLResponse)
async def revoke_tokens(
    request: Request, client_id: str, _: SameOrigin, context: Manage
) -> Response:
    """Revoke every live token; the bot must request a new one."""
    await api_access(request).revoke_tokens(context.principal, client_id)
    return redirect_done(_url(client_id), "tokens_revoked")
