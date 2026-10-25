"""Admin routes for organisation branding (WP3b, open point O5).

Every route depends on exactly one ``RequirePermission(branding.manage)``; forms and changes
are ``fresh``. Changes are POST forms with the Fetch Metadata check, answered with a redirect
and a confirmation. ``BrandingService`` authorises again, validates and audits.

    GET  /admin/branding                                  branding.manage   organisations
    GET  /admin/organisations/{org}/branding              branding.manage   form and preview
    POST /admin/organisations/{org}/branding              branding.manage   name and colour
    POST /admin/organisations/{org}/branding/logo         branding.manage   upload a logo
    POST /admin/organisations/{org}/branding/logo/remove  branding.manage   remove the logo
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import (
    Field,
    FormData,
    done_message,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.domain.authorization import Permission
from app.domain.branding import MAX_LOGO_BYTES, Branding, Logo
from app.domain.errors import DomainError
from app.domain.scheduling import Organisation
from app.web.branding import brand_view, branding_service
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

LOGO_TYPES = "image/png,image/jpeg,image/webp"

MAX_UPLOAD_OVERHEAD = 16 * 1024
"""Room for multipart headers around the logo; larger bodies are refused unread."""


Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BRANDING_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every branding page of one organisation."""


def _url(org: str) -> str:
    """Return the branding page of organisation ``org``."""
    return f"/admin/organisations/{org}/branding"


def _fields(form: FormData, organisation: Organisation, locale: RequestLocale) -> list[Field]:
    """Return the name and colour inputs, pre-filled with the current branding."""
    _ = locale.gettext
    current = organisation.branding
    return [
        Field(
            "display_name",
            _("admin.branding.display_name"),
            value=form.values.get("display_name", current.display_name or ""),
            hint=_("admin.branding.display_name_hint", name=organisation.name),
            error=form.error_for("display_name"),
            required=False,
        ),
        Field(
            "accent_color",
            _("admin.branding.accent_color"),
            "color",
            form.values.get("accent_color", current.accent_color.lower()),
            hint=_("admin.branding.accent_color_hint"),
            error=form.error_for("accent_color"),
        ),
    ]


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the branding form with the preview of what is stored now."""
    organisation = await branding_service(request).public(org)
    response = _page(
        request,
        locale,
        "admin/branding.html",
        context.principal,
        {
            "organisation": organisation,
            "brand": brand_view(organisation),
            "fields": _fields(form, organisation, locale),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
            "logo_types": LOGO_TYPES,
            "max_logo_kb": MAX_LOGO_BYTES // 1000,
        },
    )
    response.status_code = status
    return response


@router.get("/branding", response_class=HTMLResponse)
async def organisations(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.BRANDING_MANAGE))],
) -> HTMLResponse:
    """List the organisations whose branding the admin may change."""
    found = await branding_service(request).organisations(context.principal)
    return _page(
        request, locale, "admin/branding_list.html", context.principal, {"organisations": found}
    )


@router.get("/organisations/{org}/branding", response_class=HTMLResponse)
async def branding_form(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the branding form of one organisation."""
    return await _form_page(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/branding", response_class=HTMLResponse)
async def save_branding(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save display name and accent colour."""
    form = await read_form(request, locale)
    try:
        branding = Branding(form.optional_text("display_name"), form.text("accent_color"))
        changed = await branding_service(request).update(context.principal, org, branding)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "saved" if changed else "unchanged")


async def _upload(request: Request) -> bytes:
    """Read the one uploaded file, at most ``MAX_LOGO_BYTES`` plus one byte.

    Raises:
        RejectedRequest: 413 when the declared body is far above the limit (refused before
            anything is read); 400 for a malformed multipart body.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_LOGO_BYTES + MAX_UPLOAD_OVERHEAD:
        raise RejectedRequest(413)
    try:
        async with request.form(
            max_files=1, max_fields=2, max_part_size=MAX_LOGO_BYTES + 1
        ) as form:
            upload = form.get("logo")
            if not isinstance(upload, UploadFile):
                return b""
            return await upload.read(MAX_LOGO_BYTES + 1)
    except MultiPartException as exc:
        raise RejectedRequest(400) from exc


@router.post("/organisations/{org}/branding/logo", response_class=HTMLResponse)
async def upload_logo(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Store an uploaded logo; the domain checks size and type from the bytes."""
    data = await _upload(request)
    try:
        await branding_service(request).set_logo(context.principal, org, Logo.from_upload(data))
    except DomainError as error:
        raise_refusal(error)
        form = FormData({}, locale.gettext)
        form.refusal(error)
        return await _form_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "logo_saved")


@router.post("/organisations/{org}/branding/logo/remove", response_class=HTMLResponse)
async def remove_logo(request: Request, org: str, _: SameOrigin, context: Manage) -> Response:
    """Remove the logo."""
    try:
        await branding_service(request).remove_logo(context.principal, org)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(_url(org), "logo_removed")
