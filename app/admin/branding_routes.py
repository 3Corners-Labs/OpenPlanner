"""Admin routes for organisation branding (WP3b, ADR 0020 and 0041, open point O5).

Every route depends on exactly one ``RequirePermission(branding.manage)``; forms and changes
are ``fresh``. Changes are POST forms with the Fetch Metadata check, answered with a redirect
and a confirmation. ``BrandingService`` authorises again, validates and audits.

    GET  /admin/branding                                         branding.manage   organisations
    GET  /admin/organisations/{org}/branding                     branding.manage   form, preview
    POST /admin/organisations/{org}/branding                     branding.manage   name, header,
                                                                                   colours, shape
    GET  /admin/organisations/{org}/branding/theme.css           branding.manage   theme download
    POST /admin/organisations/{org}/branding/theme               branding.manage   theme upload
    POST /admin/organisations/{org}/branding/logo                branding.manage   upload a logo
    POST /admin/organisations/{org}/branding/logo/remove         branding.manage   remove the logo
    POST /admin/organisations/{org}/branding/font/{role}         branding.manage   upload a font
    POST /admin/organisations/{org}/branding/font/{role}/remove  branding.manage   remove a font
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
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
    organisation_header,
    organisation_redirect,
    with_manual,
)
from app.admin.pages import page as _page
from app.admin.uploads import read_upload
from app.domain.authorization import Permission
from app.domain.branding import (
    MAX_FONT_BYTES,
    MAX_LOGO_BYTES,
    Branding,
    FontFile,
    FontRole,
    HeaderStyle,
    Logo,
)
from app.domain.errors import DomainError
from app.domain.scheduling import Organisation
from app.domain.theme import (
    MAX_THEME_FILE_BYTES,
    PREFIX,
    REM_PX,
    SPECS,
    TOKENS,
    Theme,
    ThemeFileError,
    TokenKind,
    TokenSpec,
    normalise,
    theme_file_text,
)
from app.web.branding import brand_view, branding_service
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

LOGO_TYPES = "image/png,image/jpeg,image/webp"
FONT_TYPES = ".woff2,font/woff2"
THEME_TYPES = ".css,text/css"

FORM_TOKENS = tuple(spec for spec in TOKENS if spec.kind is not TokenKind.FONT_STACK)
"""Tokens on the form; font stacks are set by the theme file only."""

_STEPS = {"max-width": "10", "line-height": "0.1"}

HEADER_LABELS = {
    HeaderStyle.STANDARD: "admin.branding.header.standard",
    HeaderStyle.ACCENT: "admin.branding.header.accent",
    HeaderStyle.LOGO: "admin.branding.header.logo",
}
SHADOW_LABELS = {"default": "admin.branding.shadow.default", "none": "admin.branding.shadow.none"}
FONT_LABELS = {
    FontRole.TEXT: "admin.branding.font.text",
    FontRole.TEXT_BOLD: "admin.branding.font.text_bold",
    FontRole.HEADING: "admin.branding.font.heading",
}

TOKEN_TEXTS: dict[str, tuple[str, str]] = {
    "color_text": (
        "admin.branding.token.color_text",
        "admin.branding.token.color_text_hint",
    ),
    "color_text_muted": (
        "admin.branding.token.color_text_muted",
        "admin.branding.token.color_text_muted_hint",
    ),
    "color_page": (
        "admin.branding.token.color_page",
        "admin.branding.token.color_page_hint",
    ),
    "color_surface": (
        "admin.branding.token.color_surface",
        "admin.branding.token.color_surface_hint",
    ),
    "color_background": (
        "admin.branding.token.color_background",
        "admin.branding.token.color_background_hint",
    ),
    "color_border": (
        "admin.branding.token.color_border",
        "admin.branding.token.color_border_hint",
    ),
    "color_border_strong": (
        "admin.branding.token.color_border_strong",
        "admin.branding.token.color_border_strong_hint",
    ),
    "color_on_accent": (
        "admin.branding.token.color_on_accent",
        "admin.branding.token.color_on_accent_hint",
    ),
    "font_size": (
        "admin.branding.token.font_size",
        "admin.branding.token.font_size_hint",
    ),
    "line_height": (
        "admin.branding.token.line_height",
        "admin.branding.token.line_height_hint",
    ),
    "font_weight_heading": (
        "admin.branding.token.font_weight_heading",
        "admin.branding.token.font_weight_heading_hint",
    ),
    "radius": (
        "admin.branding.token.radius",
        "admin.branding.token.radius_hint",
    ),
    "radius_small": (
        "admin.branding.token.radius_small",
        "admin.branding.token.radius_small_hint",
    ),
    "max_width": (
        "admin.branding.token.max_width",
        "admin.branding.token.max_width_hint",
    ),
    "shadow": (
        "admin.branding.token.shadow",
        "admin.branding.token.shadow_hint",
    ),
}
"""Label and hint key of every token on the form, by field."""

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BRANDING_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every branding page of one organisation."""


@dataclass(frozen=True)
class FontSlot:
    """One font role on the page: its role, label, whether a font is stored, its error."""

    role: str
    label: str
    stored: bool
    error: str | None = None


def _url(org: str) -> str:
    """Return the branding page of organisation ``org``."""
    return f"/admin/organisations/{org}/branding"


def _px(value: str) -> str:
    """Return a stored length (``18px``, ``1.125rem``) as the pixel number the form shows."""
    if value.endswith("rem"):
        return f"{float(value.removesuffix('rem')) * REM_PX:g}"
    return value.removesuffix("px")


def _name_fields(form: FormData, organisation: Organisation, locale: RequestLocale) -> list[Field]:
    """Return name, header style and accent inputs, pre-filled with the current branding."""
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
            "header_style",
            _("admin.branding.header_style"),
            "select",
            form.values.get("header_style", current.header_style.value),
            hint=_("admin.branding.header_style_hint"),
            error=form.error_for("header_style"),
            options=options((s.value, _(HEADER_LABELS[s])) for s in HeaderStyle),
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


def _token_field(spec: TokenSpec, theme: Theme, form: FormData, locale: RequestLocale) -> Field:
    """Return the input of one token, pre-filled with its current value."""
    _ = locale.gettext
    current = theme.resolved(spec.name)
    label_key, key = TOKEN_TEXTS[spec.field]
    label = _(label_key)
    error = form.error_for(spec.field)
    if spec.kind is TokenKind.COLOUR:
        value = form.values.get(spec.field, current.lower())
        return Field(spec.field, label, "color", value, hint=_(key), error=error)
    if spec.kind is TokenKind.WEIGHT:
        weights = options((str(w), str(w)) for w in range(400, 1000, 100))
        value = form.values.get(spec.field, current)
        return Field(spec.field, label, "select", value, hint=_(key), options=weights, error=error)
    if spec.kind is TokenKind.SHADOW:
        choices = options((v, _(label)) for v, label in SHADOW_LABELS.items())
        value = form.values.get(spec.field, current)
        return Field(spec.field, label, "select", value, hint=_(key), options=choices, error=error)
    shown = _px(current) if spec.kind is TokenKind.LENGTH else current
    attributes = {
        "min": f"{spec.low:g}",
        "max": f"{spec.high:g}",
        "step": _STEPS.get(spec.name, "1"),
        "inputmode": "decimal",
    }
    hint = _(key, low=f"{spec.low:g}", high=f"{spec.high:g}")
    value = form.values.get(spec.field, shown)
    return Field(spec.field, label, "number", value, hint=hint, attributes=attributes, error=error)


def _token_fields(form: FormData, theme: Theme, locale: RequestLocale, kind: str) -> list[Field]:
    """Return the colour inputs (``kind="colour"``) or the shape and text inputs."""
    return [
        _token_field(spec, theme, form, locale)
        for spec in FORM_TOKENS
        if (spec.kind is TokenKind.COLOUR) == (kind == "colour")
    ]


def _font_slots(
    organisation: Organisation, form: FormData, locale: RequestLocale
) -> list[FontSlot]:
    """Return the three font roles with their labels, whether a font is stored, any error."""
    _ = locale.gettext
    return [
        FontSlot(
            role.value,
            _(FONT_LABELS[role]),
            organisation.branding.font(role) is not None,
            form.error_for(f"font-{role}"),
        )
        for role in FontRole
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
    theme = organisation.branding.theme
    response = _page(
        request,
        locale,
        "admin/branding.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "brand": brand_view(organisation),
            "name_fields": with_manual(request, locale, _name_fields(form, organisation, locale)),
            "colour_fields": with_manual(
                request, locale, _token_fields(form, theme, locale, "colour")
            ),
            "shape_fields": with_manual(
                request, locale, _token_fields(form, theme, locale, "shape")
            ),
            "fonts": _font_slots(organisation, form, locale),
            "theme_error": form.error_for("theme_file"),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
            "logo_types": LOGO_TYPES,
            "font_types": FONT_TYPES,
            "theme_types": THEME_TYPES,
            "max_logo_kb": MAX_LOGO_BYTES // 1000,
            "max_font_kb": MAX_FONT_BYTES // 1000,
            "max_theme_kb": MAX_THEME_FILE_BYTES // 1000,
        },
    )
    response.status_code = status
    return response


@router.get("/branding")
async def organisations(
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.BRANDING_MANAGE))],
) -> RedirectResponse:
    """Send the old branding list to the organisation's Branding tab (ADR 0047 §4)."""
    return organisation_redirect(context.principal, Permission.BRANDING_MANAGE, "branding")


@router.get("/organisations/{org}/branding", response_class=HTMLResponse)
async def branding_form(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the branding form of one organisation."""
    return await _form_page(request, locale, context, org, FormData({}, locale.gettext))


def _form_value(spec: TokenSpec, form: FormData, current: Theme) -> str | None:
    """Return the checked override of ``spec`` from the form; ``None`` for the default.

    A token missing from the form keeps its current override. A length in the same number
    of pixels keeps the unit it was stored with (a theme file may use ``rem``).

    Raises:
        DomainError: ``invalid_configuration`` with the token's field.
    """
    stored = current.get(spec.name)
    if spec.field not in form.values:
        return stored
    raw = form.text(spec.field)
    if not raw or (spec.kind is TokenKind.SHADOW and raw == "default"):
        return None
    value = normalise(spec.name, f"{raw}px" if spec.kind is TokenKind.LENGTH else raw)
    if spec.kind is TokenKind.LENGTH and stored is not None and _px(stored) == _px(value):
        return stored
    if spec.kind is not TokenKind.SHADOW and value == normalise(spec.name, spec.default):
        return None
    return value


def _theme_from_form(form: FormData, current: Theme) -> Theme:
    """Return the theme the form describes; invalid inputs are added to ``form.errors``.

    Font stacks are not on the form and are kept as they are.
    """
    values = {n: v for n, v in current.values if SPECS[n].kind is TokenKind.FONT_STACK}
    for spec in FORM_TOKENS:
        try:
            value = _form_value(spec, form, current)
        except DomainError as error:
            form.refusal(error)
            continue
        if value is not None:
            values[spec.name] = value
    return Theme.of(values)


@router.post("/organisations/{org}/branding", response_class=HTMLResponse)
async def save_branding(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save display name, header style, accent colour, colours and shape."""
    form = await read_form(request, locale)
    service = branding_service(request)
    current = (await service.public(org)).branding
    header = current.header_style
    if "header_style" in form.values:
        header = HeaderStyle(form.choice("header_style", set(HeaderStyle)) or header)
    theme = _theme_from_form(form, current.theme)
    if form.errors:
        return await _form_page(request, locale, context, org, form, 422)
    try:
        branding = Branding(
            form.optional_text("display_name"),
            form.text("accent_color"),
            theme=theme,
            header_style=header,
        )
        changed = await service.update(context.principal, org, branding)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _form_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "saved" if changed else "unchanged")


@router.get("/organisations/{org}/branding/theme.css")
async def download_theme(request: Request, org: str, context: Manage) -> Response:
    """Download the current theme as a theme file to edit and upload again."""
    organisation = await branding_service(request).public(org)
    branding = organisation.branding
    return Response(
        theme_file_text(branding.accent_color, branding.theme),
        media_type="text/css; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="theme-{organisation.slug}.css"',
            "cache-control": "no-store",
        },
    )


async def _refused(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    error: FormError,
) -> HTMLResponse:
    """Render the page with one upload error."""
    form = FormData({}, locale.gettext)
    form.errors.append(error)
    return await _form_page(request, locale, context, org, form, 422)


@router.post("/organisations/{org}/branding/theme", response_class=HTMLResponse)
async def upload_theme(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Replace accent and tokens by an uploaded theme file (ADR 0041 §7)."""
    gettext = locale.gettext
    data = (await read_upload(request, "theme_file", MAX_THEME_FILE_BYTES)).data
    try:
        changed = await branding_service(request).apply_theme_file(context.principal, org, data)
    except ThemeFileError as error:
        key = "admin.form.error.theme_file_line" if error.line else "admin.form.error.theme_file"
        message = gettext(key, line=error.line, kb=MAX_THEME_FILE_BYTES // 1000)
        return await _refused(request, locale, context, org, FormError("theme_file", message))
    except DomainError as error:
        raise_refusal(error)
        token = PREFIX + ("color-accent" if error.field == "accent_color" else str(error.field))
        message = gettext("admin.form.error.theme_contrast", token=token.replace("_", "-"))
        return await _refused(request, locale, context, org, FormError("theme_file", message))
    return redirect_done(_url(org), "theme_saved" if changed else "unchanged")


@router.post("/organisations/{org}/branding/logo", response_class=HTMLResponse)
async def upload_logo(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Store an uploaded logo; the domain checks size and type from the bytes."""
    data = (await read_upload(request, "logo", MAX_LOGO_BYTES)).data
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


@router.post("/organisations/{org}/branding/font/{role}", response_class=HTMLResponse)
async def upload_font(
    request: Request, locale: Locale, org: str, role: FontRole, _: SameOrigin, context: Manage
) -> Response:
    """Store an uploaded WOFF2 font for ``role``; the domain checks size and type."""
    data = (await read_upload(request, "font", MAX_FONT_BYTES)).data
    try:
        font = FontFile.from_upload(role, data, request.app.state.settings.max_font_unpacked_bytes)
        await branding_service(request).set_font(context.principal, org, font)
    except DomainError as error:
        raise_refusal(error)
        message = locale.gettext("admin.form.error.font", kb=MAX_FONT_BYTES // 1000)
        return await _refused(request, locale, context, org, FormError(f"font-{role}", message))
    return redirect_done(_url(org), "font_saved", anchor="fonts-heading")


@router.post("/organisations/{org}/branding/font/{role}/remove", response_class=HTMLResponse)
async def remove_font(
    request: Request, org: str, role: FontRole, _: SameOrigin, context: Manage
) -> Response:
    """Remove the font of ``role``."""
    try:
        await branding_service(request).remove_font(context.principal, org, role)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(_url(org), "font_removed", anchor="fonts-heading")
