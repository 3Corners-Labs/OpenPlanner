"""Admin routes for the image of a running closure (ADR 0037 §3, CL-3).

Both routes depend on exactly one ``RequirePermission(closure.manage)``, ``fresh``, and are
POST forms with the Fetch Metadata check, answered with a redirect and a confirmation. The
form sits on the group's closure page (``closure_routes``); a refusal renders that page again
with the error linked to its field.

    POST /admin/groups/{slug}/closure/image          closure.manage   upload, or change the text
    POST /admin/groups/{slug}/closure/image/remove   closure.manage   remove the image

The upload is multipart: a body far above ``MAX_IMAGE_BYTES`` is refused unread (413); the
domain recognises the type from the magic bytes and strips metadata before it is stored.
"""

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, Response

from app.admin.closure_routes import ManageClosure, closure_page_response, closure_service
from app.admin.forms import FormData, raise_refusal, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.uploads import read_upload
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ALT_LOCALES, MAX_IMAGE_BYTES, ImageFile, ImageText, alt_field
from app.web.errors import FormError, status_for

router = APIRouter(prefix="/admin", include_in_schema=False)

TEXT_FIELDS = (*(alt_field(code) for code in ALT_LOCALES), "decorative")


def _url(slug: str) -> str:
    """Return the closure page of group ``slug``."""
    return f"/admin/groups/{slug}/closure"


@router.post("/groups/{slug}/closure/image", response_class=HTMLResponse)
async def save_image(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Store an uploaded image with its text alternative, or change only the text."""
    upload = await read_upload(request, "image", MAX_IMAGE_BYTES, TEXT_FIELDS)
    form = FormData(upload.values, locale.gettext)
    try:
        text = ImageText(
            {code: form.text(alt_field(code)) for code in ALT_LOCALES},
            decorative=form.checked("decorative"),
        )
        image = ImageFile.from_upload(upload.data) if upload.data else None
        await closure_service(request).set_image(context.principal, slug, text, image)
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "image":
            message = locale.gettext("admin.form.error.image", kb=MAX_IMAGE_BYTES // 1000)
            form.errors.append(FormError("image", message))
        else:
            form.refusal(error)
        status = 422 if error.code is ErrorCode.INVALID_CONFIGURATION else status_for(error)
        return await closure_page_response(request, locale, context, form, status)
    return redirect_done(_url(slug), "image_saved")


@router.post("/groups/{slug}/closure/image/remove", response_class=HTMLResponse)
async def remove_image(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Remove the closure's image."""
    try:
        await closure_service(request).remove_image(context.principal, slug)
    except DomainError as error:
        raise_refusal(error)
        form = FormData({}, locale.gettext)
        form.refusal(error)
        return await closure_page_response(request, locale, context, form, status_for(error))
    return redirect_done(_url(slug), "image_removed")
