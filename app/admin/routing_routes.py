"""Admin routes for a group's subjects and routing (ADR 0081).

The screen lives under the group's *Settings* tab (ADR 0047, ADR 0081 point 7: group →
Settings → Subjects and routing), next to the booking form. Every route depends on one
``RequirePermission(group.edit)``, ``fresh`` like the rest of the settings. Changes are POST
forms with the Fetch Metadata check, answered with a redirect and a confirmation.
``RoutingService`` authorises again, validates and audits.

    GET  /admin/groups/{slug}/settings/routing                     group.edit  the page
    POST /admin/groups/{slug}/settings/routing/subjects            group.edit  add a subject
    GET  /admin/groups/{slug}/settings/routing/subjects/{code}     group.edit  edit its labels
    POST /admin/groups/{slug}/settings/routing/subjects/{code}     group.edit  save them
    POST /admin/groups/{slug}/settings/routing/subjects/{code}/move    group.edit
    POST /admin/groups/{slug}/settings/routing/subjects/{code}/remove  group.edit
    POST /admin/groups/{slug}/settings/routing/attributes          group.edit  switch on/off
    POST /admin/groups/{slug}/settings/routing/mapping/{source}    group.edit  one variable

No JavaScript: every form works on its own.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, with_manual
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import (
    ATTRIBUTE_VALUES,
    REQUIRED_LOCALES,
    SUBJECT_SOURCE,
    Attribute,
    GroupRouting,
    RouteVariable,
    Subject,
)
from app.domain.routing_service import GroupRoutingView, RoutingService
from app.i18n import BoundGettext
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.routing_labels import question_key, value_key

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
]
"""The permission of the routing settings, as for the rest of the group's settings."""

LABEL_KEYS: dict[str, str] = {
    "nl": "admin.routing.subject.label_nl",
    "en": "admin.routing.subject.label_en",
}
ERROR_KEYS: dict[str, tuple[str, str]] = {
    "subject_code": ("code", "admin.routing.error.subject_code"),
    "subject_label": ("label_nl", "admin.routing.error.subject_label"),
    "subject_label_nl": ("label_nl", "admin.routing.error.subject_label"),
    "subject_label_en": ("label_en", "admin.routing.error.subject_label"),
    "subjects": ("code", "admin.routing.error.subjects"),
    "subject_in_use": ("code", "admin.routing.error.subject_in_use"),
    "variable_name": ("variable", "admin.routing.error.variable_name"),
    "variable_value": ("variable", "admin.routing.error.variable_value"),
    "mapping": ("variable", "admin.routing.error.mapping"),
}
"""Per refused attribute: the input the message is linked to and the message."""
ATTRIBUTE_HELP: dict[Attribute, str] = {
    Attribute.PREFERRED_LANGUAGE: "admin.routing.attribute.preferred_language.help",
    Attribute.CHANNEL_PREFERENCE: "admin.routing.attribute.channel_preference.help",
}


def routing_service(request: Request) -> RoutingService:
    """Return the application's routing service."""
    service: RoutingService = request.app.state.routing_service
    return service


def _url(slug: str) -> str:
    """Return the routing page of group ``slug``."""
    return f"/admin/groups/{slug}/settings/routing"


def _refusal(form: FormData, error: DomainError) -> None:
    """Add the message for a refused change, linked to its input."""
    if error.code is ErrorCode.INVALID_CONFIGURATION and error.field in ERROR_KEYS:
        target, key = ERROR_KEYS[error.field]
        form.errors.append(FormError(target, form._(key)))
    else:
        form.refusal(error)


def _labels(form: FormData) -> dict[str, str]:
    """Return the submitted labels per locale."""
    return {locale: form.text(f"label_{locale}") for locale in REQUIRED_LOCALES}


def _subject_inputs(_: BoundGettext, form: FormData, current: Subject | None) -> list[Field]:
    """Return the inputs of the add or edit form of a subject; the code only when adding."""

    def value(name: str, stored: str) -> str:
        return form.values.get(name, stored) if form.values else stored

    fields = [
        Field(
            f"label_{locale}",
            _(LABEL_KEYS[locale]),
            value=value(f"label_{locale}", dict(current.labels).get(locale, "") if current else ""),
            error=form.error_for(f"label_{locale}"),
            attributes={"maxlength": "80"},
        )
        for locale in REQUIRED_LOCALES
    ]
    if current is None:
        fields.append(
            Field(
                "code",
                _("admin.routing.subject.code"),
                value=value("code", ""),
                hint=_("admin.routing.subject.code_hint"),
                error=form.error_for("code"),
                attributes={"maxlength": "40", "autocomplete": "off", "spellcheck": "false"},
            )
        )
    return fields


def _source_label(_: BoundGettext, source: str) -> str:
    """Return the name of a mapping source for the administrator."""
    if source == SUBJECT_SOURCE:
        return _("admin.routing.source.subject")
    return _(question_key(source))


def _code_label(routing: GroupRouting, source: str, code: str, locale: RequestLocale) -> str:
    """Return the label of one code of ``source``."""
    if source == SUBJECT_SOURCE:
        subject = routing.subject(code)
        return subject.label(locale.code) if subject else code
    return locale.gettext(value_key(source, code))


def _mappings(
    found: GroupRoutingView, locale: RequestLocale, form: FormData, posted: str | None
) -> list[dict[str, object]]:
    """Return one mapping form per source, filled from the stored or the refused input."""
    routing = found.routing
    views: list[dict[str, object]] = []
    for source in routing.sources:
        variable = routing.variable(source)
        mine = posted == source and bool(form.values)

        def value(name: str, stored: str, mine: bool = mine) -> str:
            return form.values.get(name, stored) if mine else stored

        views.append(
            {
                "source": source,
                "label": _source_label(locale.gettext, source),
                "variable": value(f"variable_{source}", variable.name if variable else ""),
                "error": form.error_for(f"variable_{source}") if mine else None,
                "codes": [
                    {
                        "code": code,
                        "label": _code_label(routing, source, code, locale),
                        "value": value(
                            f"value_{source}_{code}",
                            (variable.value(code) or "") if variable else "",
                        ),
                    }
                    for code in routing.codes(source)
                ],
            }
        )
    return views


async def _page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    form: FormData,
    status: int = 200,
    posted: str | None = None,
) -> HTMLResponse:
    """Render the subjects, the attribute switches and the mapping forms.

    ``posted`` names the form that was refused (``subject``, ``attributes`` or a source), so
    only that one shows the submitted values and the errors.
    """
    _ = locale.gettext
    found = await routing_service(request).view(context.principal, slug)
    routing = found.routing
    subjects = routing.subjects
    rows = [
        {
            "code": s.code,
            "label": s.label(locale.code),
            "first": index == 0,
            "last": index == len(subjects) - 1,
            "bound": s.code in found.bound,
        }
        for index, s in enumerate(subjects)
    ]
    subject_form = form if posted == SUBJECT_SOURCE + "_add" else FormData({}, _)
    return group_page(
        request,
        locale,
        context,
        "admin/group_routing.html",
        {
            "rows": rows,
            "can_add": found.can_add,
            "limit": found.max_subjects,
            "subject_fields": with_manual(request, locale, _subject_inputs(_, subject_form, None)),
            "attributes": [
                {
                    "value": a.value,
                    "label": _(question_key(a.value)),
                    "help": _(ATTRIBUTE_HELP[a]),
                    "values": ", ".join(_(value_key(a.value, c)) for c in ATTRIBUTE_VALUES[a]),
                    "on": a in routing.attributes,
                }
                for a in Attribute
            ],
            "mappings": _mappings(found, locale, form, posted),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/settings/routing", response_class=HTMLResponse)
async def routing_page(request: Request, locale: Locale, slug: str, context: Manage) -> Response:
    """Show the group's subjects, routing attributes and mapping."""
    return await _page(request, locale, context, slug, FormData({}, locale.gettext))


@router.post("/groups/{slug}/settings/routing/subjects", response_class=HTMLResponse)
async def add_subject(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: Manage
) -> Response:
    """Add a subject at the end of the list."""
    form = await read_form(request, locale)
    try:
        subject = Subject.create(form.text("code"), _labels(form))
        await routing_service(request).add_subject(context.principal, slug, subject)
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error)
        return await _page(request, locale, context, slug, form, 422, SUBJECT_SOURCE + "_add")
    return redirect_done(_url(slug), "created", anchor="subjects")


async def _edit_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    form: FormData,
    status: int = 200,
) -> Response:
    """Render the form of one subject's labels."""
    found = await routing_service(request).view(context.principal, slug)
    current = found.routing.subject(code)
    if current is None:
        return redirect_done(_url(slug), "unchanged")
    return group_page(
        request,
        locale,
        context,
        "admin/group_routing_subject.html",
        {
            "subject_code": current.code,
            "subject_label": current.label(locale.code),
            "fields": with_manual(request, locale, _subject_inputs(locale.gettext, form, current)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/settings/routing/subjects/{code}", response_class=HTMLResponse)
async def edit_subject(
    request: Request, locale: Locale, slug: str, code: str, context: Manage
) -> Response:
    """Show the labels of one subject."""
    return await _edit_page(request, locale, context, slug, code, FormData({}, locale.gettext))


@router.post("/groups/{slug}/settings/routing/subjects/{code}", response_class=HTMLResponse)
async def save_subject(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the labels of one subject; its code stays."""
    form = await read_form(request, locale)
    try:
        await routing_service(request).update_subject(context.principal, slug, code, _labels(form))
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error)
        return await _edit_page(request, locale, context, slug, code, form, 422)
    return redirect_done(_url(slug), "saved", anchor="subjects")


@router.post("/groups/{slug}/settings/routing/subjects/{code}/move", response_class=HTMLResponse)
async def move_subject(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Move one subject a place up or down."""
    form = await read_form(request, locale)
    step = -1 if form.text("direction") == "up" else 1
    try:
        await routing_service(request).move_subject(context.principal, slug, code, step)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_url(slug), "unchanged")
    return redirect_done(_url(slug), "saved", anchor="subjects")


@router.post("/groups/{slug}/settings/routing/subjects/{code}/remove", response_class=HTMLResponse)
async def remove_subject(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Remove one subject; bookings that chose it keep its code."""
    form = FormData({}, locale.gettext)
    try:
        await routing_service(request).remove_subject(context.principal, slug, code)
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error)
        return await _page(request, locale, context, slug, form, 422)
    return redirect_done(_url(slug), "saved", anchor="subjects")


@router.post("/groups/{slug}/settings/routing/attributes", response_class=HTMLResponse)
async def save_attributes(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: Manage
) -> Response:
    """Switch the catalogue's attributes on or off."""
    form = await read_form(request, locale)
    chosen = {a for a in Attribute if form.checked(f"attribute_{a.value}")}
    try:
        await routing_service(request).set_attributes(context.principal, slug, chosen)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_url(slug), "unchanged")
    return redirect_done(_url(slug), "saved", anchor="attributes")


@router.post("/groups/{slug}/settings/routing/mapping/{source}", response_class=HTMLResponse)
async def save_mapping(
    request: Request, locale: Locale, slug: str, source: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the routing variable of one source; an empty name sends nothing for it."""
    form = await read_form(request, locale)
    service = routing_service(request)
    found = await service.view(context.principal, slug)
    if source not in found.routing.sources:
        return redirect_done(_url(slug), "unchanged")
    name = form.text(f"variable_{source}")
    try:
        variable = (
            RouteVariable(
                name,
                tuple(
                    (code, form.text(f"value_{source}_{code}"))
                    for code in found.routing.codes(source)
                    if form.text(f"value_{source}_{code}")
                ),
            )
            if name
            else None
        )
        await service.set_mapping(context.principal, slug, source, variable)
    except DomainError as error:
        raise_refusal(error)
        _refusal(form, error)
        form.errors = [
            FormError(f"variable_{source}", e.message) if e.field == "variable" else e
            for e in form.errors
        ]
        return await _page(request, locale, context, slug, form, 422, source)
    return redirect_done(_url(slug), "saved", anchor="mapping")
