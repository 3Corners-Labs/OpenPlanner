"""Admin routes for embedding: the websites allowed to show an organisation's groups (ADR 0035).

Every route depends on exactly one ``RequirePermission(embedding.manage)`` (organisation
admin and superuser); the form is ``fresh``. The change is a POST form with the Fetch
Metadata check, answered with a redirect and a confirmation. ``EmbeddingService`` authorises
again, validates and audits.

    GET  /admin/embedding                    embedding.manage   organisations in scope
    GET  /admin/organisations/{org}/embedding  embedding.manage   origins and groups
    POST /admin/organisations/{org}/embedding  embedding.manage   save; empty removes all

The origins are one textarea, one per line (no JavaScript). Whether a group appears is its
own setting (``group.edit``); this page lists the groups and links to their settings, and
shows the code a host copies: the script tag with its SRI hash and one element per group
that is switched on.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import (
    Locale,
    SameOrigin,
    organisation_header,
    organisation_redirect,
    page,
    with_manual,
)
from app.domain.authorization import Permission
from app.domain.embedding import MAX_ORIGIN_LENGTH
from app.domain.embedding_service import EmbeddingService
from app.domain.errors import DomainError, ErrorCode
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.widget_bundle import SCRIPT_URL_PATH, load_bundle

router = APIRouter(prefix="/admin", include_in_schema=False)

ORIGINS_FIELD = "origins"

List = Annotated[AdminContext, Depends(RequirePermission(Permission.EMBEDDING_MANAGE))]
Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.EMBEDDING_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of the embedding page of one organisation."""

_LINE_FIELD = re.compile(r"origin_([0-9]+)")


def embedding_service(request: Request) -> EmbeddingService:
    """Return the application's embedding service."""
    service: EmbeddingService = request.app.state.embedding_service
    return service


def _url(org: str) -> str:
    """Return the embedding page of organisation ``org``."""
    return f"/admin/organisations/{org}/embedding"


def _refusal(form: FormData, error: DomainError, locale: RequestLocale, limit: int) -> None:
    """Add the message for a refused line or list, linked to the textarea."""
    _ = locale.gettext
    found = _LINE_FIELD.fullmatch(error.field or "")
    if error.code is ErrorCode.INVALID_CONFIGURATION and found:
        message = _("admin.embedding.error.origin", line=found[1])
        form.errors.append(FormError(ORIGINS_FIELD, message))
    elif error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "origins_count":
        form.errors.append(FormError(ORIGINS_FIELD, _("admin.embedding.error.count", count=limit)))
    else:
        form.refusal(error)


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the origins form with the organisation's groups and their embedding status."""
    _ = locale.gettext
    service = embedding_service(request)
    found = await service.view(context.principal, org)
    stored = "\n".join(found.allowed.origins)
    origins = Field(
        ORIGINS_FIELD,
        _("admin.embedding.origins"),
        "textarea",
        form.values.get(ORIGINS_FIELD, stored),
        hint=_(
            "admin.embedding.origins_hint_local"
            if service.allow_local
            else "admin.embedding.origins_hint",
            count=service.max_origins,
        ),
        error=form.error_for(ORIGINS_FIELD),
        required=False,
        attributes={
            "rows": "6",
            "spellcheck": "false",
            "autocapitalize": "none",
            "maxlength": str((MAX_ORIGIN_LENGTH + 1) * service.max_origins),
        },
    )
    base = request.app.state.settings.public_base_url.rstrip("/")
    bundle = load_bundle()
    response = page(
        request,
        locale,
        "admin/organisation_embedding.html",
        context.principal,
        {
            "organisation": found.organisation,
            "org_header": organisation_header(
                request, locale, context.principal, found.organisation.name, found.organisation.slug
            ),
            "origins": found.allowed.origins,
            "groups": found.groups,
            "embedded": found.embedded_groups,
            "script_url": f"{base}{SCRIPT_URL_PATH}",
            "integrity": bundle.integrity if bundle else None,
            "origins_field": with_manual(request, locale, [origins])[0],
            "errors": form.errors,
            "done": done_message(request, _),
        },
    )
    response.status_code = status
    return response


@router.get("/embedding")
async def organisations(context: List) -> RedirectResponse:
    """Send the old embedding list to the organisation's Embedding sub-tab (ADR 0047 §4)."""
    return organisation_redirect(context.principal, Permission.EMBEDDING_MANAGE, "embedding")


@router.get("/organisations/{org}/embedding", response_class=HTMLResponse)
async def embedding_form(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the allowed origins of one organisation."""
    return await _form_page(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/embedding", response_class=HTMLResponse)
async def save_embedding(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the allowed origins; an empty list removes them all."""
    form = await read_form(request, locale)
    service = embedding_service(request)
    try:
        changed = await service.save(context.principal, org, form.text(ORIGINS_FIELD))
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error, locale, service.max_origins)
        return await _form_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "saved" if changed else "unchanged")
