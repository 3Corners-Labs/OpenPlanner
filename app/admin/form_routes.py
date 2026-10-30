"""Admin routes for a group's booking form: the custom fields citizens fill in (ADR 0055).

The screen lives under the group's *Settings* tab (ADR 0047: group → Settings → Booking form).
Every route depends on one ``RequirePermission(group.edit)``, ``fresh`` like the rest of the
settings. Changes are POST forms with the Fetch Metadata check, answered with a redirect and a
confirmation. ``FormService`` authorises again, validates and audits.

    GET  /admin/groups/{slug}/settings/form               group.edit   fields and the add form
    POST /admin/groups/{slug}/settings/form               group.edit   add a field
    GET  /admin/groups/{slug}/settings/form/{key}         group.edit   edit one field
    POST /admin/groups/{slug}/settings/form/{key}         group.edit   save it
    POST /admin/groups/{slug}/settings/form/{key}/move    group.edit   one place up or down
    POST /admin/groups/{slug}/settings/form/{key}/retire  group.edit   retire it

No JavaScript: the options of a choice are one textarea, a line per option. Labels and help
texts are asked in Dutch and English, the two locales every label needs.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, options, raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, with_manual
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    ALWAYS_PII,
    REQUIRED_LOCALES,
    Classification,
    FieldDefinition,
    FieldType,
    format_options,
    parse_options,
)
from app.domain.form_service import FormService
from app.i18n import BoundGettext
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
]
"""The permission of the booking form, as for the rest of the group's settings."""

TYPE_LABELS: dict[FieldType, str] = {
    FieldType.SHORT_TEXT: "admin.form_fields.type.short_text",
    FieldType.LONG_TEXT: "admin.form_fields.type.long_text",
    FieldType.EMAIL: "admin.form_fields.type.email",
    FieldType.PHONE: "admin.form_fields.type.phone",
    FieldType.NUMBER: "admin.form_fields.type.number",
    FieldType.DATE: "admin.form_fields.type.date",
    FieldType.POSTCODE_NL: "admin.form_fields.type.postcode_nl",
    FieldType.CHOICE: "admin.form_fields.type.choice",
    FieldType.CHECKBOX: "admin.form_fields.type.checkbox",
}
CLASSIFICATION_LABELS: dict[Classification, str] = {
    Classification.PII: "admin.form_fields.classification.pii",
    Classification.NOT_PII: "admin.form_fields.classification.not_pii",
}
LABEL_KEYS: dict[str, str] = {
    "nl": "admin.form_fields.label_nl",
    "en": "admin.form_fields.label_en",
}
HINT_KEYS: dict[str, str] = {"nl": "admin.form_fields.hint_nl", "en": "admin.form_fields.hint_en"}
BOUND_KEYS: dict[str, tuple[str, str]] = {
    "minimum": ("admin.form_fields.minimum", "admin.form_fields.minimum_hint"),
    "maximum": ("admin.form_fields.maximum", "admin.form_fields.maximum_hint"),
}
"""Catalogue keys, written out literally for the catalogue test."""

ERROR_KEYS: dict[str, str] = {
    "key": "admin.form_fields.error.key",
    "type": "admin.form_fields.error.type",
    "label": "admin.form_fields.error.label",
    "hint": "admin.form_fields.error.hint",
    "classification": "admin.form_fields.error.classification",
    "confirm_not_pii": "admin.form_fields.error.confirm_not_pii",
    "options": "admin.form_fields.error.options",
    "minimum": "admin.form_fields.error.bounds",
    "maximum": "admin.form_fields.error.bounds",
    "fields": "admin.form_fields.error.limit",
}
"""The message of each refused attribute."""

FIELD_FOR: dict[str, str] = {
    "label": "label_nl",
    "hint": "hint_nl",
    "fields": "key",
}
"""The input an attribute's error is linked to, where the names differ."""


def form_service(request: Request) -> FormService:
    """Return the application's booking form service."""
    service: FormService = request.app.state.form_service
    return service


def _url(slug: str) -> str:
    """Return the booking form page of group ``slug``."""
    return f"/admin/groups/{slug}/settings/form"


def _refusal(form: FormData, error: DomainError) -> None:
    """Add the message for a refused definition, linked to its input."""
    if error.code is ErrorCode.FORM_FIELD_FORBIDDEN:
        target = FIELD_FOR.get(error.field or "", error.field or "label_nl")
        form.errors.append(FormError(target, form._("admin.form.error.form_field_forbidden")))
    elif error.code is ErrorCode.INVALID_FORM_FIELD and error.field in ERROR_KEYS:
        target = FIELD_FOR.get(error.field, error.field)
        form.errors.append(FormError(target, form._(ERROR_KEYS[error.field])))
    else:
        form.refusal(error)


def _bound(form: FormData, name: str) -> int | None:
    """Return a whole number (negative allowed, for days in the past), or ``None`` if empty."""
    raw = form.text(name).strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit() and len(raw) <= 7:
        return int(raw)
    form.errors.append(FormError(name, form._(ERROR_KEYS["minimum"])))
    return None


def _definition(form: FormData, key: str, kind: FieldType) -> FieldDefinition | None:
    """Return the definition the form describes, or ``None`` with the errors on ``form``."""
    minimum, maximum = _bound(form, "minimum"), _bound(form, "maximum")
    classification = form.text("classification")
    if classification not in {c.value for c in Classification} or form.errors:
        if not form.errors:
            form.errors.append(FormError("classification", form._(ERROR_KEYS["classification"])))
        return None
    try:
        keys, option_labels = (
            parse_options(form.text("options")) if kind is FieldType.CHOICE else ((), {})
        )
        return FieldDefinition.create(
            key=key,
            type=kind,
            labels={locale: form.text(f"label_{locale}") for locale in REQUIRED_LOCALES},
            hints={locale: form.text(f"hint_{locale}") for locale in REQUIRED_LOCALES},
            required=form.checked("required"),
            classification=Classification(classification),
            options=keys,
            option_labels=option_labels,
            minimum=minimum if kind in (FieldType.NUMBER, FieldType.DATE) else None,
            maximum=maximum if kind in (FieldType.NUMBER, FieldType.DATE) else None,
        )
    except DomainError as error:
        _refusal(form, error)
        return None


def _inputs(_: BoundGettext, form: FormData, current: FieldDefinition | None) -> list[Field]:
    """Return the inputs of the add or edit form, filled from ``form`` or ``current``."""

    def value(name: str, stored: str) -> str:
        return form.values.get(name, stored) if form.values else stored

    fields: list[Field] = []
    if current is None:
        fields += [
            Field(
                "key",
                _("admin.form_fields.key"),
                value=value("key", ""),
                hint=_("admin.form_fields.key_hint"),
                error=form.error_for("key"),
                attributes={"maxlength": "40", "autocomplete": "off", "spellcheck": "false"},
            ),
            Field(
                "type",
                _("admin.form_fields.type"),
                "select",
                value("type", FieldType.SHORT_TEXT.value),
                hint=_("admin.form_fields.type_hint"),
                error=form.error_for("type"),
                options=options((t.value, _(TYPE_LABELS[t])) for t in FieldType),
            ),
        ]
    for locale in REQUIRED_LOCALES:
        fields.append(
            Field(
                f"label_{locale}",
                _(LABEL_KEYS[locale]),
                value=value(f"label_{locale}", current.labels.get(locale, "") if current else ""),
                error=form.error_for(f"label_{locale}"),
                attributes={"maxlength": "200"},
            )
        )
    for locale in REQUIRED_LOCALES:
        fields.append(
            Field(
                f"hint_{locale}",
                _(HINT_KEYS[locale]),
                value=value(f"hint_{locale}", current.hints.get(locale, "") if current else ""),
                error=form.error_for(f"hint_{locale}"),
                required=False,
                attributes={"maxlength": "300"},
            )
        )
    stored_class = current.classification.value if current else Classification.PII.value
    fields += [
        Field(
            "classification",
            _("admin.form_fields.classification"),
            "select",
            value("classification", stored_class),
            hint=_("admin.form_fields.classification_hint"),
            error=form.error_for("classification"),
            options=options((c.value, _(CLASSIFICATION_LABELS[c])) for c in Classification),
        ),
    ]
    if current is None or current.type is FieldType.CHOICE:
        fields.append(
            Field(
                "options",
                _("admin.form_fields.options"),
                "textarea",
                value("options", format_options(current) if current else ""),
                hint=_("admin.form_fields.options_hint"),
                error=form.error_for("options"),
                required=False,
                attributes={"rows": "5", "spellcheck": "false"},
            )
        )
    if current is None or current.type in (FieldType.NUMBER, FieldType.DATE):
        for name in ("minimum", "maximum"):
            stored = getattr(current, name) if current else None
            fields.append(
                Field(
                    name,
                    _(BOUND_KEYS[name][0]),
                    value=value(name, "" if stored is None else str(stored)),
                    hint=_(BOUND_KEYS[name][1]),
                    error=form.error_for(name),
                    required=False,
                    attributes={"inputmode": "numeric", "maxlength": "7"},
                )
            )
    return fields


async def _page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the list of fields and the form to add one."""
    _ = locale.gettext
    found = await form_service(request).view(context.principal, slug)
    rows = [
        {
            "key": f.key,
            "label": f.label(locale.code),
            "type": _(TYPE_LABELS[f.type]),
            "required": f.required,
            "pii": f.classification is Classification.PII,
            "first": index == 0,
            "last": index == len(found.form.fields) - 1,
        }
        for index, f in enumerate(found.form.fields)
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/group_form_fields.html",
        {
            "rows": rows,
            "retired": [f.label(locale.code) for f in found.form.retired],
            "can_add": found.can_add,
            "limit": found.max_custom_fields,
            "always_pii": False,
            "fields": with_manual(request, locale, _inputs(_, form, None)),
            "required": form.checked("required") if form.values else False,
            "confirm": form.checked("confirm_not_pii"),
            "confirm_error": form.error_for("confirm_not_pii"),
            "errors": form.errors,
        },
        status,
    )


async def _edit_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    key: str,
    form: FormData,
    status: int = 200,
) -> Response:
    """Render the form of one active field."""
    _ = locale.gettext
    found = await form_service(request).view(context.principal, slug)
    current = found.form.get(key)
    if current is None:
        return redirect_done(_url(slug), "unchanged")
    return group_page(
        request,
        locale,
        context,
        "admin/group_form_field.html",
        {
            "field_key": current.key,
            "edit_lead": _(
                "admin.form_fields.edit_lead",
                field_key=current.key,
                type=_(TYPE_LABELS[current.type]),
            ),
            "field_label": current.label(locale.code),
            "always_pii": current.type in ALWAYS_PII,
            "fields": with_manual(request, locale, _inputs(_, form, current)),
            "required": form.checked("required") if form.values else current.required,
            "confirm": form.checked("confirm_not_pii"),
            "confirm_error": form.error_for("confirm_not_pii"),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/settings/form", response_class=HTMLResponse)
async def booking_form(request: Request, locale: Locale, slug: str, context: Manage) -> Response:
    """Show the group's custom fields and the form to add one."""
    return await _page(request, locale, context, slug, FormData({}, locale.gettext))


@router.post("/groups/{slug}/settings/form", response_class=HTMLResponse)
async def add_field(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: Manage
) -> Response:
    """Add a field at the end of the form."""
    form = await read_form(request, locale)
    kind = form.choice("type", {t.value for t in FieldType})
    definition = _definition(form, form.text("key").strip(), FieldType(kind)) if kind else None
    if definition is not None:
        try:
            await form_service(request).add(
                context.principal, slug, definition, confirmed=form.checked("confirm_not_pii")
            )
        except DomainError as error:
            raise_refusal(error)
            _refusal(form, error)
        else:
            return redirect_done(_url(slug), "created")
    return await _page(request, locale, context, slug, form, 422)


@router.get("/groups/{slug}/settings/form/{key}", response_class=HTMLResponse)
async def edit_field(
    request: Request, locale: Locale, slug: str, key: str, context: Manage
) -> Response:
    """Show the form of one field."""
    return await _edit_page(request, locale, context, slug, key, FormData({}, locale.gettext))


@router.post("/groups/{slug}/settings/form/{key}", response_class=HTMLResponse)
async def save_field(
    request: Request, locale: Locale, slug: str, key: str, _: SameOrigin, context: Manage
) -> Response:
    """Save one field; its key and type stay."""
    form = await read_form(request, locale)
    service = form_service(request)
    current = (await service.view(context.principal, slug)).form.get(key)
    if current is None:
        return redirect_done(_url(slug), "unchanged")
    definition = _definition(form, key, current.type)
    if definition is not None:
        try:
            changed = await service.update(
                context.principal, slug, definition, confirmed=form.checked("confirm_not_pii")
            )
        except DomainError as error:
            raise_refusal(error)
            _refusal(form, error)
        else:
            return redirect_done(_url(slug), "saved" if changed else "unchanged")
    return await _edit_page(request, locale, context, slug, key, form, 422)


@router.post("/groups/{slug}/settings/form/{key}/move", response_class=HTMLResponse)
async def move_field(
    request: Request, locale: Locale, slug: str, key: str, _: SameOrigin, context: Manage
) -> Response:
    """Move one field a place up or down."""
    form = await read_form(request, locale)
    step = -1 if form.text("direction") == "up" else 1
    try:
        await form_service(request).move(context.principal, slug, key, step)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_url(slug), "unchanged")
    return redirect_done(_url(slug), "saved", anchor="fields")


@router.post("/groups/{slug}/settings/form/{key}/retire", response_class=HTMLResponse)
async def retire_field(
    request: Request, locale: Locale, slug: str, key: str, _: SameOrigin, context: Manage
) -> Response:
    """Retire one field: no new answers; earlier answers keep its label until purged."""
    try:
        await form_service(request).retire(context.principal, slug, key)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_url(slug), "unchanged")
    return redirect_done(_url(slug), "field_retired", anchor="fields")
