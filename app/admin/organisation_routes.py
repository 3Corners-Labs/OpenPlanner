"""Admin routes for the organisations list and the organisation's own page (ADR 0047 step 3).

Reading goes through ``organisation.view`` within the admin's reach: the superuser sees every
organisation, an organisation admin and the auditor their own. An admin who may open exactly
one organisation is sent straight to it from the list (ADR 0047 §2). Creating and editing
stay with the superuser (``organisation.manage``, HANDOVER §6); those forms and changes are
``fresh``. Changes are POST forms with the Fetch Metadata check, answered with a redirect and
a confirmation. ``OrganisationService`` authorises again, validates and audits.

    GET  /admin/organisations             organisation.view     list, or straight to the one
    GET  /admin/organisations/new         organisation.manage   form for a new organisation
    POST /admin/organisations             organisation.manage   create
    GET  /admin/organisations/{org}       organisation.view     overview: groups and profile
    GET  /admin/organisations/{org}/edit  organisation.manage   edit form
    POST /admin/organisations/{org}       organisation.manage   save name, default language and
                                                                the IdP value (ADR 0049 point 4)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, admin_service
from app.admin.forms import (
    Field,
    FormData,
    Option,
    done_message,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin, organisation_header, page
from app.domain.authorization import Permission, scope_for
from app.domain.entitlements import IDP_KEY_PATTERN
from app.domain.errors import DomainError
from app.domain.organisations import NewOrganisation, OrganisationService, OrganisationSettings
from app.domain.scheduling import Organisation
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

LIST_URL = "/admin/organisations"

View = Annotated[AdminContext, Depends(RequirePermission(Permission.ORGANISATION_VIEW))]
"""Reading the list: at least one organisation in reach."""

Read = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.ORGANISATION_VIEW, organisation_param="org")),
]
"""Reading one organisation's page."""

Manage = Annotated[
    AdminContext, Depends(RequirePermission(Permission.ORGANISATION_MANAGE, fresh=True))
]
"""Forms and changes: only after a recent login (ADR 0015)."""


def organisation_service(request: Request) -> OrganisationService:
    """Return the application's organisation service."""
    service: OrganisationService = request.app.state.organisation_service
    return service


def _languages(locale: RequestLocale) -> tuple[Option, ...]:
    """Return every locale with a catalogue, labelled with its own name."""
    translator = locale.translator
    return tuple(
        Option(code, translator.lookup(code, "language.autonym")) for code in translator.locales
    )


def _fields(
    form: FormData,
    locale: RequestLocale,
    current: Organisation | None = None,
    holders: int = 0,
) -> list[Field]:
    """Return the inputs; without ``current`` (a new organisation) the slug is editable.

    An existing organisation also gets its IdP value (ADR 0049 point 4), with a warning
    naming how many people (``holders``) lose access until the IdP sends the new value.
    """
    _ = locale.gettext
    fields = [
        Field(
            "name",
            _("admin.field.name"),
            value=form.values.get("name", current.name if current else ""),
            error=form.error_for("name"),
        ),
    ]
    if current is None:
        fields.append(
            Field(
                "slug",
                _("admin.organisation.slug"),
                value=form.text("slug"),
                hint=_("admin.organisation.slug_hint"),
                error=form.error_for("slug"),
                attributes={"autocapitalize": "none", "spellcheck": "false"},
            )
        )
    fields.append(
        Field(
            "default_locale",
            _("admin.field.default_locale"),
            "select",
            form.values.get("default_locale", current.default_locale if current else "nl"),
            hint=_("admin.field.default_locale_hint"),
            error=form.error_for("default_locale"),
            options=_languages(locale),
        )
    )
    if current is not None:
        hint = _("admin.organisation.idp_key_hint")
        if holders:
            hint = f"{hint} {_('admin.idp_mapping.holders_warning', count=holders)}"
        fields.append(
            Field(
                "idp_key",
                _("admin.idp_mapping.idp_key"),
                value=form.values.get("idp_key", current.idp_key),
                hint=hint,
                error=form.error_for("idp_key"),
                attributes={
                    "autocapitalize": "none",
                    "spellcheck": "false",
                    "pattern": IDP_KEY_PATTERN,
                    "maxlength": "63",
                },
            )
        )
    return fields


def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData,
    current: Organisation | None = None,
    status: int = 200,
    holders: int = 0,
) -> HTMLResponse:
    """Render the form for a new (``current`` is ``None``) or an existing organisation."""
    header = (
        organisation_header(request, locale, context.principal, current.name, current.slug)
        if current
        else None
    )
    response = page(
        request,
        locale,
        "admin/organisation_form.html",
        context.principal,
        {
            "organisation": current,
            "org_header": header,
            "heading": current.name if current else locale.gettext("admin.new_organisation.title"),
            "fields": _fields(form, locale, current, holders),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


@router.get("/organisations", response_class=HTMLResponse)
async def organisations(request: Request, locale: Locale, context: View) -> Response:
    """List the organisations in reach; an admin of exactly one goes straight to it."""
    found = await organisation_service(request).organisations(context.principal)
    scope = scope_for(context.principal, Permission.ORGANISATION_VIEW)
    if not scope.everything and len(found) == 1:
        return RedirectResponse(f"{LIST_URL}/{found[0].slug}", status_code=303)
    names = {o.value: o.label for o in _languages(locale)}
    return page(
        request,
        locale,
        "admin/organisations.html",
        context.principal,
        {"organisations": found, "language_names": names},
    )


@router.get("/organisations/new", response_class=HTMLResponse)
async def new_organisation(request: Request, locale: Locale, context: Manage) -> HTMLResponse:
    """Show the form for a new organisation."""
    return _form_page(request, locale, context, FormData({}, locale.gettext))


@router.post("/organisations", response_class=HTMLResponse)
async def create_organisation(
    request: Request, locale: Locale, _: SameOrigin, context: Manage
) -> Response:
    """Create an organisation, then show it."""
    form = await read_form(request, locale)
    new = NewOrganisation(form.text("name"), form.text("slug"), form.text("default_locale"))
    try:
        created = await organisation_service(request).create(context.principal, new)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return _form_page(request, locale, context, form, status=422)
    return redirect_done(f"{LIST_URL}/{created.slug}", "created")


@router.get("/organisations/{org}", response_class=HTMLResponse)
async def organisation(request: Request, locale: Locale, org: str, context: Read) -> HTMLResponse:
    """The organisation's overview tab: its groups in the admin's reach, and its profile."""
    principal = context.principal
    current = await organisation_service(request).view(principal, org)
    groups = [
        g for g in await admin_service(request).groups(principal) if g.organisation_slug == org
    ]
    names = {o.value: o.label for o in _languages(locale)}
    return page(
        request,
        locale,
        "admin/organisation.html",
        principal,
        {
            "organisation": current,
            "org_header": organisation_header(request, locale, principal, current.name, org),
            "groups": groups,
            "language_name": names.get(current.default_locale, current.default_locale),
            "done": done_message(request, locale.gettext),
        },
    )


@router.get("/organisations/{org}/edit", response_class=HTMLResponse)
async def organisation_form(
    request: Request, locale: Locale, org: str, context: Manage
) -> HTMLResponse:
    """Show the edit form of one organisation (name, default language, IdP value)."""
    service = organisation_service(request)
    current = await service.get(context.principal, org)
    holders = await service.holders_of(context.principal, org)
    return _form_page(request, locale, context, FormData({}, locale.gettext), current, 200, holders)


@router.post("/organisations/{org}", response_class=HTMLResponse)
async def save_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save name, default language and the IdP value; the slug never changes."""
    service = organisation_service(request)
    current = await service.get(context.principal, org)
    form = await read_form(request, locale)
    settings = OrganisationSettings(
        form.text("name"), form.text("default_locale"), form.text("idp_key") or None
    )
    try:
        changed = await service.update(context.principal, org, settings)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        holders = await service.holders_of(context.principal, org)
        return _form_page(request, locale, context, form, current, 422, holders)
    return redirect_done(f"{LIST_URL}/{org}", "saved" if changed else "unchanged")
