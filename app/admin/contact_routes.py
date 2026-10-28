"""Admin routes for contact blocks: organisation default and group override (ADR 0037 §2).

Every route depends on one ``RequirePermission``, ``fresh`` like every closure page: what
citizens read in an emergency is prepared here. The change is a POST form with the Fetch
Metadata check, answered with a redirect and a confirmation. ``ContactService`` authorises
again, validates and audits.

    GET  /admin/organisations/{org}/contacts     organisation.closure   form and preview
    POST /admin/organisations/{org}/contacts     organisation.closure   save; all rows empty removes
    GET  /admin/groups/{slug}/closure/contacts   closure.manage         the group's own block
    POST /admin/groups/{slug}/closure/contacts   closure.manage         save; empty: default applies

The form has ``MAX_OPTIONS`` fixed rows (no JavaScript): kind, details, and a label and opening
hours per locale. A row whose inputs are all empty is skipped.
"""

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin import forms
from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import done_message, raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, page
from app.domain.authorization import Permission
from app.domain.contacts import (
    CONTACT_LOCALES,
    MAX_HEADING_LENGTH,
    MAX_HOURS_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_OPTIONS,
    MAX_URL_LENGTH,
    ContactBlock,
    ContactKind,
    ContactOption,
    numbered_option,
)
from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext
from app.web.contact_labels import contact_service, contact_view, kind_label
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.ORGANISATION_CLOSURE, organisation_param="org", fresh=True)
    ),
]
"""The permission of the contact block page of one organisation."""

ManageGroup = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CLOSURE_MANAGE, group_param="slug", fresh=True)),
]
"""The permission of a group's own contact block, as for the rest of its closure page."""

HEADING_LABELS: dict[str, str] = {
    "nl": "admin.contacts.heading_nl",
    "en": "admin.contacts.heading_en",
}
LABEL_LABELS: dict[str, str] = {
    "nl": "admin.contacts.label_nl",
    "en": "admin.contacts.label_en",
}
HOURS_LABELS: dict[str, str] = {
    "nl": "admin.contacts.hours_nl",
    "en": "admin.contacts.hours_en",
}
"""Input labels per locale (literal keys for the catalogue test)."""

PART_ERRORS: dict[str, tuple[str, str]] = {
    "kind": ("kind", "admin.contacts.error.kind"),
    "phone": ("value", "admin.contacts.error.phone"),
    "email": ("value", "admin.contacts.error.email"),
    "url": ("value", "admin.contacts.error.url"),
    "address": ("value", "admin.contacts.error.address"),
    "label": ("label_nl", "admin.contacts.error.label"),
    "hours": ("hours_nl", "admin.contacts.error.hours"),
}
"""Per refused part of a row: the input the message is linked to, and the message."""

_ROW_FIELD = re.compile(r"contact_([0-9]+)_([a-z]+)")


@dataclass(frozen=True)
class ContactRow:
    """The inputs of one form row, for the template."""

    number: int
    fields: list[forms.Field]


def _url(org: str) -> str:
    """Return the contact block page of organisation ``org``."""
    return f"/admin/organisations/{org}/contacts"


def _group_url(slug: str) -> str:
    """Return the contact block page of group ``slug``."""
    return f"/admin/groups/{slug}/closure/contacts"


def _name(number: int, part: str) -> str:
    """Return the input name of ``part`` in row ``number`` (``contact_2_value``)."""
    return f"contact_{number}_{part}"


def _row(
    number: int, option: ContactOption | None, form: forms.FormData, locale: RequestLocale
) -> ContactRow:
    """Return the inputs of row ``number``, pre-filled from ``option`` or the submitted form."""
    _ = locale.gettext

    def value(part: str, stored: str) -> str:
        return form.values.get(_name(number, part), stored)

    kinds = (forms.Option("", _("admin.contacts.kind_unused")),) + tuple(
        forms.Option(kind.value, kind_label(kind, _)) for kind in ContactKind
    )
    fields = [
        forms.Field(
            _name(number, "kind"),
            _("admin.contacts.kind"),
            "select",
            value("kind", option.kind.value if option else ""),
            options=kinds,
            error=form.error_for(_name(number, "kind")),
            required=False,
        ),
        forms.Field(
            _name(number, "value"),
            _("admin.contacts.value"),
            "textarea",
            value("value", option.value if option else ""),
            hint=_("admin.contacts.value_hint"),
            error=form.error_for(_name(number, "value")),
            required=False,
            attributes={"rows": "2", "maxlength": str(MAX_URL_LENGTH)},
        ),
    ]
    for prefix, labels, limit, stored in (
        ("label", LABEL_LABELS, MAX_LABEL_LENGTH, option.labels if option else {}),
        ("hours", HOURS_LABELS, MAX_HOURS_LENGTH, option.hours if option else {}),
    ):
        fields += [
            forms.Field(
                _name(number, f"{prefix}_{code}"),
                _(labels[code]),
                value=value(f"{prefix}_{code}", stored.get(code, "")),
                error=form.error_for(_name(number, f"{prefix}_{code}")),
                required=False,
                attributes={"maxlength": str(limit)},
            )
            for code in CONTACT_LOCALES
        ]
    return ContactRow(number, fields)


def _heading_fields(
    block: ContactBlock | None, form: forms.FormData, locale: RequestLocale
) -> list[forms.Field]:
    """Return the optional heading inputs, one per locale."""
    _ = locale.gettext
    stored = block.headings if block else {}
    return [
        forms.Field(
            f"heading_{code}",
            _(HEADING_LABELS[code]),
            value=form.values.get(f"heading_{code}", stored.get(code, "")),
            hint=_("admin.contacts.heading_hint"),
            error=form.error_for(f"heading_{code}"),
            required=False,
            attributes={"maxlength": str(MAX_HEADING_LENGTH)},
        )
        for code in CONTACT_LOCALES
    ]


def _block(form: forms.FormData) -> ContactBlock:
    """Return the submitted block; rows whose inputs are all empty are skipped.

    Raises:
        DomainError: ``invalid_configuration`` naming the row and part, or the heading.
    """
    options = []
    for number in range(1, MAX_OPTIONS + 1):
        labels = {code: form.text(_name(number, f"label_{code}")) for code in CONTACT_LOCALES}
        hours = {code: form.text(_name(number, f"hours_{code}")) for code in CONTACT_LOCALES}
        kind, value = form.text(_name(number, "kind")), form.text(_name(number, "value"))
        if not (kind or value or any(labels.values()) or any(hours.values())):
            continue
        options.append(numbered_option(number, kind, value, labels, hours))
    headings = {code: form.text(f"heading_{code}") for code in CONTACT_LOCALES}
    return ContactBlock(tuple(options), headings)


def _refusal(form: forms.FormData, error: DomainError, gettext: BoundGettext) -> None:
    """Add the message for a refused row or heading, linked to its input."""
    found = _ROW_FIELD.fullmatch(error.field or "")
    if error.code is ErrorCode.INVALID_CONFIGURATION and found and found[2] in PART_ERRORS:
        part, key = PART_ERRORS[found[2]]
        target = _name(int(found[1]), part)
        form.errors.append(FormError(target, gettext(key, row=found[1])))
    elif error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "contacts_heading":
        form.errors.append(FormError("heading_nl", gettext("admin.contacts.error.heading")))
    else:
        form.refusal(error)


def _form_values(
    block: ContactBlock | None, form: forms.FormData, locale: RequestLocale
) -> dict[str, object]:
    """Return the template values of the form, pre-filled from ``block`` or the submission."""
    options = block.options if block else ()
    rows = [
        _row(n, options[n - 1] if n <= len(options) else None, form, locale)
        for n in range(1, MAX_OPTIONS + 1)
    ]
    return {
        "heading_fields": _heading_fields(block, form, locale),
        "rows": rows,
        "errors": form.errors,
    }


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: forms.FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the organisation's contact block form with the preview of what is stored now."""
    found = await contact_service(request).view(context.principal, org)
    response = page(
        request,
        locale,
        "admin/organisation_contacts.html",
        context.principal,
        {
            "organisation": found.organisation,
            "preview": contact_view(found.block, locale),
            "done": done_message(request, locale.gettext),
            **_form_values(found.block, form, locale),
        },
    )
    response.status_code = status
    return response


async def _group_form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: forms.FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the group's own block form; the preview shows the block that applies now."""
    found = await contact_service(request).view_group(context.principal, context.group.group.slug)
    default = found.organisation_default
    return group_page(
        request,
        locale,
        context,
        "admin/group_contacts.html",
        {
            "own": found.block is not None,
            "default_count": len(default.options) if default else 0,
            "preview": contact_view(found.block or default, locale),
            **_form_values(found.block, form, locale),
        },
        status,
    )


@router.get("/organisations/{org}/contacts", response_class=HTMLResponse)
async def contacts_form(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the contact block form of one organisation."""
    return await _form_page(request, locale, context, org, forms.FormData({}, locale.gettext))


@router.post("/organisations/{org}/contacts", response_class=HTMLResponse)
async def save_contacts(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the contact block; with every row empty it is removed."""
    form = await read_form(request, locale)
    try:
        changed = await contact_service(request).save(context.principal, org, _block(form))
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error, locale.gettext)
        return await _form_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "saved" if changed else "unchanged")


@router.get("/groups/{slug}/closure/contacts", response_class=HTMLResponse)
async def group_contacts_form(
    request: Request, locale: Locale, slug: str, context: ManageGroup
) -> Response:
    """Show the form of the group's own contact block."""
    return await _group_form_page(request, locale, context, forms.FormData({}, locale.gettext))


@router.post("/groups/{slug}/closure/contacts", response_class=HTMLResponse)
async def save_group_contacts(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Save the group's own block; with every row empty the organisation default applies."""
    form = await read_form(request, locale)
    try:
        changed = await contact_service(request).save_group(context.principal, slug, _block(form))
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error, locale.gettext)
        return await _group_form_page(request, locale, context, form, 422)
    return redirect_done(_group_url(slug), "saved" if changed else "unchanged")
