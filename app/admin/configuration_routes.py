"""Exporting, planning and applying a configuration document in the portal (ADR 0099 point 13).

The platform's block sits on *Advanced settings › Configuration package*, the organisation's
on *Overview › Configuration*. Both do the same three things on their own scope: export a
document (with secrets sealed under a passphrase, or without), plan an uploaded one and show
the plan as a table, and apply it with the hash of the plan that was shown. The upload is kept
for the apply (``ReviewedDocuments``), so the admin applies exactly what they reviewed.

    GET  /admin/organisations/{org}/configuration   the organisation's block
                                                    (settings.organisation.manage)
    POST /admin/organisations/{org}/configuration/export|plan|apply
                                                    export, upload → plan, apply (recent login)
    POST /admin/settings/configuration/export|plan|apply
                                                    the same for the platform
                                                    (settings.platform.manage, recent login)

A document of another scope than the route's is refused (``configuration_out_of_scope``);
every change is checked against the matrix per object; a passphrase is read from the form and
never echoed, logged or stored.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.adapters.configuration_json import MEDIA_TYPE
from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.configuration_view import (
    DOCUMENT_KEY,
    block_home,
    document_block,
    plan_view,
)
from app.admin.forms import CODE_MESSAGES, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, organisation_header
from app.admin.pages import page as _page
from app.admin.reviewed_documents import ReviewedDocuments
from app.admin.settings_routes import Tab, render_tab
from app.admin.uploads import read_upload
from app.config import Settings
from app.domain.authorization import Permission
from app.domain.configuration_document import Scope, document_filename
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.errors import DomainError, ErrorCode
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

UPLOAD_FIELDS = ("passphrase",)
"""The text field that comes with an uploaded document."""
PLAN_TEMPLATE = "admin/configuration_plan.html"

PlatformChange = Annotated[
    AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_MANAGE, fresh=True))
]
OrganisationView = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SETTINGS_ORGANISATION_MANAGE, organisation_param="org")),
]
OrganisationChange = Annotated[
    AdminContext,
    Depends(
        RequirePermission(
            Permission.SETTINGS_ORGANISATION_MANAGE, organisation_param="org", fresh=True
        )
    ),
]


def _service(request: Request) -> ConfigurationDocumentService:
    """Return the application's configuration document service."""
    service: ConfigurationDocumentService = request.app.state.configuration_documents
    return service


def _reviewed(request: Request) -> ReviewedDocuments:
    """Return the store of the documents under review."""
    store: ReviewedDocuments = request.app.state.reviewed_documents
    return store


def _actor(context: AdminContext) -> Actor:
    """Return the acting admin as the document service takes them."""
    return Actor(context.principal.subject, context.principal)


# --- the organisation's page ------------------------------------------------------------


@router.get("/organisations/{org}/configuration", response_class=HTMLResponse)
async def organisation_configuration(
    request: Request, locale: Locale, org: str, context: OrganisationView
) -> HTMLResponse:
    """Show the organisation's block: export a document, upload one to plan."""
    return await _render(
        request, locale, context, Scope(org), "admin/organisation_configuration.html"
    )


@router.post("/organisations/{org}/configuration/export")
async def export_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: OrganisationChange
) -> Response:
    """Return the organisation's document."""
    return await _export(request, locale, context, Scope(org))


@router.post("/organisations/{org}/configuration/plan", response_class=HTMLResponse)
async def plan_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: OrganisationChange
) -> Response:
    """Plan an uploaded document for the organisation and show the plan."""
    return await _plan(request, locale, context, Scope(org))


@router.post("/organisations/{org}/configuration/apply", response_class=HTMLResponse)
async def apply_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: OrganisationChange
) -> Response:
    """Apply the reviewed document to the organisation."""
    return await _apply(request, locale, context, Scope(org))


# --- the platform's routes (their page is the package tab of Advanced settings) ---------


@router.post("/settings/configuration/export")
async def export_platform(
    request: Request, locale: Locale, _: SameOrigin, context: PlatformChange
) -> Response:
    """Return the platform's document."""
    return await _export(request, locale, context, Scope())


@router.post("/settings/configuration/plan", response_class=HTMLResponse)
async def plan_platform(
    request: Request, locale: Locale, _: SameOrigin, context: PlatformChange
) -> Response:
    """Plan an uploaded platform document and show the plan."""
    return await _plan(request, locale, context, Scope())


@router.post("/settings/configuration/apply", response_class=HTMLResponse)
async def apply_platform(
    request: Request, locale: Locale, _: SameOrigin, context: PlatformChange
) -> Response:
    """Apply the reviewed platform document."""
    return await _apply(request, locale, context, Scope())


# --- the three actions, for either scope ------------------------------------------------


async def _export(
    request: Request, locale: RequestLocale, context: AdminContext, scope: Scope
) -> Response:
    """Export the configuration of ``scope``; with secrets only under a repeated passphrase."""
    form = await read_form(request, locale)
    plain = form.text("plain") == "yes"
    secrets = form.text("secrets") == "yes" and not plain
    passphrase = form.text("passphrase")
    try:
        if plain and form.text("plain_confirm") != "yes":
            raise DomainError(ErrorCode.CONFIGURATION_PLAINTEXT_UNCONFIRMED, DOCUMENT_KEY)
        if secrets and passphrase != form.text("passphrase_repeat"):
            raise DomainError(ErrorCode.CONFIRMATION_MISMATCH, DOCUMENT_KEY)
        document = await _service(request).export(
            _actor(context), scope, passphrase if secrets else None, secrets=secrets, plain=plain
        )
    except DomainError as error:
        raise_refusal(error)
        return await _fail(request, locale, context, scope, error, 422)
    current: Settings = request.app.state.settings
    name = document_filename(current.installation_name, request.app.state.clock.now())
    return Response(
        document,
        media_type=MEDIA_TYPE,
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "cache-control": "no-store",
        },
    )


async def _plan(
    request: Request, locale: RequestLocale, context: AdminContext, scope: Scope
) -> Response:
    """Plan the uploaded document; keep it for the apply and show the plan."""
    current: Settings = request.app.state.settings
    upload = await read_upload(
        request, DOCUMENT_KEY, current.configuration_package_max_mb * 1024 * 1024, UPLOAD_FIELDS
    )
    try:
        if not upload.data:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, DOCUMENT_KEY)
        plan = await _service(request).plan(
            _actor(context),
            upload.data,
            upload.values.get("passphrase", "").strip() or None,
            scope=scope,
        )
    except DomainError as error:
        raise_refusal(error)
        return await _fail(request, locale, context, scope, error, 422)
    entry = _reviewed(request).keep(
        context.principal.subject,
        scope,
        upload.data,
        plan.digest,
        request.app.state.clock.now(),
        current.configuration_review_minutes,
    )
    return await _render(
        request,
        locale,
        context,
        scope,
        PLAN_TEMPLATE,
        {
            "plan": plan_view(plan, locale.gettext),
            "review_id": entry.token,
            "needs_seal": plan.sealed,
        },
    )


async def _apply(
    request: Request, locale: RequestLocale, context: AdminContext, scope: Scope
) -> Response:
    """Apply the document that was reviewed, if it is the one the token names."""
    form = await read_form(request, locale)
    subject = context.principal.subject
    reviewed = _reviewed(request)
    entry = reviewed.find(subject, scope, form.text("token"), request.app.state.clock.now())
    if entry is None:
        stale = DomainError(ErrorCode.CONFIGURATION_PLAN_STALE)
        return await _fail(request, locale, context, scope, stale, 409)
    try:
        result = await _service(request).apply(
            _actor(context),
            entry.data,
            form.text("passphrase") or None,
            entry.plan_hash,
            scope=scope,
        )
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.CONFIGURATION_PLAN_STALE:
            reviewed.discard(subject, scope)
        return await _fail(request, locale, context, scope, error, 409)
    reviewed.discard(subject, scope)
    if not result.applied:
        shown = {
            "plan": plan_view(result.plan, locale.gettext),
            "review_id": "",
            "needs_seal": False,
        }
        return await _render(request, locale, context, scope, PLAN_TEMPLATE, shown, 422)
    return redirect_done(block_home(scope), "configuration_applied", len(result.plan.changes))


# --- rendering -------------------------------------------------------------------------


async def _fail(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    scope: Scope,
    error: DomainError,
    status: int,
) -> Response:
    """Show the scope's page again with the refusal; the platform's is the package tab."""
    if scope.is_platform:
        return await render_tab(
            request, locale, context, Tab.PACKAGE, (DOCUMENT_KEY, error), status
        )
    message = locale.gettext(CODE_MESSAGES.get(error.code, "error.invalid_request"))
    return await _render(
        request,
        locale,
        context,
        scope,
        "admin/organisation_configuration.html",
        error=message,
        status=status,
    )


async def _render(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    scope: Scope,
    template: str,
    values: dict[str, object] | None = None,
    status: int = 200,
    *,
    error: str | None = None,
) -> HTMLResponse:
    """Render a page of the block under the header of the scope's object."""
    can_seal = can_function(context.principal)(Permission.SETTINGS_PLATFORM_MANAGE)
    shared: dict[str, object] = {
        "document": document_block(
            request.app.state.settings, scope, can_seal=can_seal, error=error
        ),
        "done": done_message(request, locale.gettext),
        # The platform pages use the settings header, which reads these.
        "refused": None,
        "unknown_error": None,
        "org_header": None,
    }
    if not scope.is_platform:
        organisation = await request.app.state.settings_service.organisation(
            context.principal, str(scope.organisation)
        )
        shared["organisation"] = organisation
        shared["org_header"] = organisation_header(
            request, locale, context.principal, organisation.name, organisation.slug
        )
    response = _page(request, locale, template, context.principal, {**shared, **(values or {})})
    response.status_code = status
    return response
