"""Admin routes for prepared closure scenarios (ADR 0037 §4, CL-4).

The same screens exist for an organisation (``organisation.closure``) and for one group
(``closure.manage``); each route depends on exactly one ``RequirePermission``, ``fresh`` like
every closure page. Changes are POST forms with the Fetch Metadata check, answered with a
redirect and a confirmation. ``ScenarioService`` authorises again, checks whose the scenario is
and audits.

    {base} = /admin/organisations/{org}/closure   or   /admin/groups/{slug}/closure

    GET  {base}/scenarios                       list, and the form for a new scenario
    POST {base}/scenarios                       create
    GET  {base}/scenarios/{id}                  edit an own one; view an organisation one
    POST {base}/scenarios/{id}                  name, notices, headings
    POST {base}/scenarios/{id}/delete           delete
    GET  {base}/scenarios/{id}/contacts         the scenario's contact block
    POST {base}/scenarios/{id}/contacts         save; all rows empty: the group's block applies
    POST {base}/scenarios/{id}/image            upload, or change the text (multipart)
    POST {base}/scenarios/{id}/image/remove     remove the image
    GET  {base}/scenarios/{id}/image            the image, for admins only (never public)

A group admin sees the organisation's scenarios read-only, with a link to start a closure
from one; changing them is ``scenario_not_found`` through the group and ``not_permitted``
through the organisation.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin import forms
from app.admin.authz import AdminContext, RequirePermission
from app.admin.closure_routes import (
    headings_of,
    notices_of,
    scenario_service,
    scenario_url,
    text_fields,
)
from app.admin.contact_routes import block_form_values, block_refusal, read_block
from app.admin.forms import Field, FormData, raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.image_forms import decorative, image_fields, image_refusal, read_image_form
from app.admin.pages import Locale, SameOrigin, page, with_manual
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.scenario_service import ScenarioInput
from app.domain.scenarios import MAX_NAME_LENGTH, ClosureScenario, ScenarioOwner
from app.web.closure_labels import ImageView
from app.web.contact_labels import contact_service, contact_view
from app.web.errors import status_for
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

ManageOrganisation = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.ORGANISATION_CLOSURE, organisation_param="org", fresh=True)
    ),
]
ManageGroup = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CLOSURE_MANAGE, group_param="slug", fresh=True)),
]

NO_STORE = {"cache-control": "private, no-store"}
"""Scenario images are not public (ADR 0037 §3): never cached by anyone else."""


@dataclass(frozen=True)
class ScenarioView:
    """A scenario as the list and the edit page show it.

    Attributes:
        id: The scenario.
        name: Its name.
        url: Its edit (or view) page.
        editable: Whether this owner may change it (not an organisation one seen by a group).
        use_url: The closing form started from it.
    """

    id: int
    name: str
    url: str
    editable: bool
    use_url: str


def _owner_of(org: str | None = None, slug: str | None = None) -> ScenarioOwner:
    """Return the owner named by the route."""
    return ScenarioOwner(organisation=org, group=slug)


def _closure_url(owner: ScenarioOwner) -> str:
    """Return the closure page of the owner."""
    if owner.group is not None:
        return f"/admin/groups/{owner.group}/closure"
    return f"/admin/organisations/{owner.organisation}/closure"


def _view(owner: ScenarioOwner, scenario: ClosureScenario) -> ScenarioView:
    """Return the list entry of ``scenario`` for ``owner``."""
    scenario_id = scenario.id or 0
    editable = scenario.is_organisation == (owner.group is None)
    return ScenarioView(
        scenario_id,
        scenario.name,
        f"{scenario_url(owner)}/{scenario_id}",
        editable,
        f"{_closure_url(owner)}?scenario={scenario_id}",
    )


async def _render(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    template: str,
    extra: dict[str, object],
    status: int = 200,
) -> HTMLResponse:
    """Render a scenario page: with the group's header and tabs, or the organisation's."""
    values = {
        "scenarios_url": scenario_url(owner),
        "closure_url": _closure_url(owner),
        "organisation_slug": owner.organisation,
        **extra,
    }
    if owner.group is not None:
        return group_page(request, locale, context, template, values, status)
    found = await contact_service(request).view(context.principal, owner.organisation or "")
    values["organisation_name"] = found.organisation.name
    values["done"] = forms.done_message(request, locale.gettext)
    response = page(request, locale, template, context.principal, values)
    response.status_code = status
    return response


def _name_field(form: FormData, locale: RequestLocale, stored: str) -> Field:
    """Return the input of the scenario's name."""
    _ = locale.gettext
    return Field(
        "scenario_name",
        _("admin.scenarios.name"),
        value=form.values.get("scenario_name", stored),
        hint=_("admin.scenarios.name_hint", limit=MAX_NAME_LENGTH),
        error=form.error_for("scenario_name"),
        attributes={"maxlength": str(MAX_NAME_LENGTH)},
    )


def _texts(form: FormData, locale: RequestLocale, scenario: ClosureScenario | None) -> list[Field]:
    """Return the name, notice and heading inputs, pre-filled from ``scenario``."""
    notices = dict(scenario.notices) if scenario else {}
    headings = dict(scenario.headings) if scenario else {}
    return [
        _name_field(form, locale, scenario.name if scenario else ""),
        *text_fields(form, locale, notices, headings),
    ]


def _paragraphs(scenario: ClosureScenario, locale: str) -> list[str]:
    """Return the scenario's notice in ``locale`` (Dutch as fallback), split into paragraphs."""
    notice = scenario.notices.get(locale) or scenario.notices["nl"]
    return [p for p in notice.split("\n") if p.strip()]


def _input(form: FormData) -> ScenarioInput:
    """Read the scenario form."""
    return ScenarioInput(form.text("scenario_name"), notices_of(form), headings_of(form))


# --- list and create ------------------------------------------------------------------------


async def _list_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the owner's scenarios and the form for a new one."""
    listed = await scenario_service(request).list(context.principal, owner)
    return await _render(
        request,
        locale,
        context,
        owner,
        "admin/scenarios.html",
        {
            "own": [_view(owner, s) for s in listed.own],
            "shared": [_view(owner, s) for s in listed.organisation],
            "is_group": owner.group is not None,
            "fields": with_manual(request, locale, _texts(form, locale, None)),
            "errors": form.errors,
        },
        status,
    )


async def _create(
    request: Request, locale: RequestLocale, context: AdminContext, owner: ScenarioOwner
) -> Response:
    """Create a scenario and go to its page, where contacts and image are added."""
    form = await read_form(request, locale)
    try:
        created = await scenario_service(request).save(context.principal, owner, _input(form))
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _list_page(request, locale, context, owner, form, 422)
    return redirect_done(f"{scenario_url(owner)}/{created.id}", "scenario_created")


@router.get("/organisations/{org}/closure/scenarios", response_class=HTMLResponse)
async def organisation_list(
    request: Request, locale: Locale, org: str, context: ManageOrganisation
) -> HTMLResponse:
    """Show the organisation's scenarios."""
    form = FormData({}, locale.gettext)
    return await _list_page(request, locale, context, _owner_of(org=org), form)


@router.get("/groups/{slug}/closure/scenarios", response_class=HTMLResponse)
async def group_list(
    request: Request, locale: Locale, slug: str, context: ManageGroup
) -> HTMLResponse:
    """Show the group's own scenarios and the organisation's."""
    form = FormData({}, locale.gettext)
    return await _list_page(request, locale, context, _owner_of(slug=slug), form)


@router.post("/organisations/{org}/closure/scenarios", response_class=HTMLResponse)
async def organisation_create(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: ManageOrganisation
) -> Response:
    """Create an organisation scenario."""
    return await _create(request, locale, context, _owner_of(org=org))


@router.post("/groups/{slug}/closure/scenarios", response_class=HTMLResponse)
async def group_create(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Create a scenario of the group."""
    return await _create(request, locale, context, _owner_of(slug=slug))


# --- one scenario ---------------------------------------------------------------------------


async def _scenario_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    scenario_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render one scenario: its forms when the owner may change it, else a preview."""
    try:
        scenario = await scenario_service(request).get(context.principal, owner, scenario_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    view = _view(owner, scenario)
    found = scenario.image
    src = f"{view.url}/image?v={found.digest}" if found else ""
    image = ImageView(src, found.text.alt(locale.code)) if found else None
    return await _render(
        request,
        locale,
        context,
        owner,
        "admin/scenario.html",
        {
            "scenario": view,
            "preview_heading": scenario.headings.get(locale.code) or scenario.headings.get("nl"),
            "preview_paragraphs": _paragraphs(scenario, locale.code),
            "contacts": contact_view(scenario.contacts, locale),
            "contact_count": len(scenario.contacts.options) if scenario.contacts else 0,
            "image": image,
            "fields": with_manual(request, locale, _texts(form, locale, scenario)),
            "image_fields": with_manual(request, locale, image_fields(form, locale, found)),
            "image_decorative": decorative(form, found),
            "errors": form.errors,
        },
        status,
    )


async def _save(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    scenario_id: int,
) -> Response:
    """Save the name and texts of an own scenario."""
    form = await read_form(request, locale)
    try:
        await scenario_service(request).save(context.principal, owner, _input(form), scenario_id)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        status = 422 if error.code is ErrorCode.INVALID_CONFIGURATION else status_for(error)
        return await _scenario_page(request, locale, context, owner, scenario_id, form, status)
    return redirect_done(f"{scenario_url(owner)}/{scenario_id}", "saved")


async def _delete(
    request: Request, context: AdminContext, owner: ScenarioOwner, scenario_id: int
) -> Response:
    """Delete an own scenario and go back to the list."""
    try:
        await scenario_service(request).delete(context.principal, owner, scenario_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(scenario_url(owner), "scenario_deleted")


@router.get("/organisations/{org}/closure/scenarios/{scenario_id}", response_class=HTMLResponse)
async def organisation_scenario(
    request: Request, locale: Locale, org: str, scenario_id: int, context: ManageOrganisation
) -> HTMLResponse:
    """Show one organisation scenario."""
    form = FormData({}, locale.gettext)
    return await _scenario_page(request, locale, context, _owner_of(org=org), scenario_id, form)


@router.get("/groups/{slug}/closure/scenarios/{scenario_id}", response_class=HTMLResponse)
async def group_scenario(
    request: Request, locale: Locale, slug: str, scenario_id: int, context: ManageGroup
) -> HTMLResponse:
    """Show one scenario of the group, or one of the organisation's (read-only)."""
    form = FormData({}, locale.gettext)
    return await _scenario_page(request, locale, context, _owner_of(slug=slug), scenario_id, form)


@router.post("/organisations/{org}/closure/scenarios/{scenario_id}", response_class=HTMLResponse)
async def organisation_save(
    request: Request,
    locale: Locale,
    org: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageOrganisation,
) -> Response:
    """Save an organisation scenario."""
    return await _save(request, locale, context, _owner_of(org=org), scenario_id)


@router.post("/groups/{slug}/closure/scenarios/{scenario_id}", response_class=HTMLResponse)
async def group_save(
    request: Request,
    locale: Locale,
    slug: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageGroup,
) -> Response:
    """Save a scenario of the group."""
    return await _save(request, locale, context, _owner_of(slug=slug), scenario_id)


@router.post(
    "/organisations/{org}/closure/scenarios/{scenario_id}/delete", response_class=HTMLResponse
)
async def organisation_delete(
    request: Request, org: str, scenario_id: int, _: SameOrigin, context: ManageOrganisation
) -> Response:
    """Delete an organisation scenario."""
    return await _delete(request, context, _owner_of(org=org), scenario_id)


@router.post("/groups/{slug}/closure/scenarios/{scenario_id}/delete", response_class=HTMLResponse)
async def group_delete(
    request: Request, slug: str, scenario_id: int, _: SameOrigin, context: ManageGroup
) -> Response:
    """Delete a scenario of the group."""
    return await _delete(request, context, _owner_of(slug=slug), scenario_id)


# --- contacts -------------------------------------------------------------------------------


async def _contacts_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    scenario_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the contact block form of an own scenario."""
    try:
        scenario = await scenario_service(request).get(context.principal, owner, scenario_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    view = _view(owner, scenario)
    if not view.editable:
        raise DomainError(ErrorCode.SCENARIO_NOT_FOUND)
    return await _render(
        request,
        locale,
        context,
        owner,
        "admin/scenario_contacts.html",
        {
            "scenario": view,
            "preview": contact_view(scenario.contacts, locale),
            **block_form_values(scenario.contacts, form, locale),
        },
        status,
    )


async def _save_contacts(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    scenario_id: int,
) -> Response:
    """Save the scenario's contact block; with every row empty the group's block applies."""
    form = await read_form(request, locale)
    try:
        block = read_block(form)
        await scenario_service(request).save_contacts(context.principal, owner, scenario_id, block)
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.SCENARIO_NOT_FOUND:
            raise
        block_refusal(form, error, locale.gettext)
        return await _contacts_page(request, locale, context, owner, scenario_id, form, 422)
    return redirect_done(f"{scenario_url(owner)}/{scenario_id}", "saved")


@router.get(
    "/organisations/{org}/closure/scenarios/{scenario_id}/contacts", response_class=HTMLResponse
)
async def organisation_contacts(
    request: Request, locale: Locale, org: str, scenario_id: int, context: ManageOrganisation
) -> HTMLResponse:
    """Show the contact block form of an organisation scenario."""
    form = FormData({}, locale.gettext)
    return await _contacts_page(request, locale, context, _owner_of(org=org), scenario_id, form)


@router.get("/groups/{slug}/closure/scenarios/{scenario_id}/contacts", response_class=HTMLResponse)
async def group_contacts(
    request: Request, locale: Locale, slug: str, scenario_id: int, context: ManageGroup
) -> HTMLResponse:
    """Show the contact block form of a scenario of the group."""
    form = FormData({}, locale.gettext)
    return await _contacts_page(request, locale, context, _owner_of(slug=slug), scenario_id, form)


@router.post(
    "/organisations/{org}/closure/scenarios/{scenario_id}/contacts", response_class=HTMLResponse
)
async def organisation_save_contacts(
    request: Request,
    locale: Locale,
    org: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageOrganisation,
) -> Response:
    """Save the contact block of an organisation scenario."""
    return await _save_contacts(request, locale, context, _owner_of(org=org), scenario_id)


@router.post("/groups/{slug}/closure/scenarios/{scenario_id}/contacts", response_class=HTMLResponse)
async def group_save_contacts(
    request: Request,
    locale: Locale,
    slug: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageGroup,
) -> Response:
    """Save the contact block of a scenario of the group."""
    return await _save_contacts(request, locale, context, _owner_of(slug=slug), scenario_id)


# --- image ----------------------------------------------------------------------------------


async def _save_image(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    owner: ScenarioOwner,
    scenario_id: int,
) -> Response:
    """Store an uploaded image of an own scenario, or change only its text."""
    submitted = await read_image_form(request, locale)
    try:
        text, image = submitted.parsed()
        await scenario_service(request).set_image(
            context.principal, owner, scenario_id, text, image
        )
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.SCENARIO_NOT_FOUND:
            raise
        status = image_refusal(submitted.form, error, locale) or status_for(error)
        return await _scenario_page(
            request, locale, context, owner, scenario_id, submitted.form, status
        )
    return redirect_done(f"{scenario_url(owner)}/{scenario_id}", "image_saved")


async def _remove_image(
    request: Request, context: AdminContext, owner: ScenarioOwner, scenario_id: int
) -> Response:
    """Remove the image of an own scenario."""
    try:
        await scenario_service(request).remove_image(context.principal, owner, scenario_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"{scenario_url(owner)}/{scenario_id}", "image_removed")


async def _image(
    request: Request, context: AdminContext, owner: ScenarioOwner, scenario_id: int
) -> Response:
    """Serve a scenario's image to the admin; never cached, never public."""
    try:
        found = await scenario_service(request).image(context.principal, owner, scenario_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    if found is None:
        return Response(status_code=404, headers=NO_STORE)
    return Response(found.data, media_type=found.media_type, headers=NO_STORE)


@router.post(
    "/organisations/{org}/closure/scenarios/{scenario_id}/image", response_class=HTMLResponse
)
async def organisation_save_image(
    request: Request,
    locale: Locale,
    org: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageOrganisation,
) -> Response:
    """Store the image of an organisation scenario."""
    return await _save_image(request, locale, context, _owner_of(org=org), scenario_id)


@router.post("/groups/{slug}/closure/scenarios/{scenario_id}/image", response_class=HTMLResponse)
async def group_save_image(
    request: Request,
    locale: Locale,
    slug: str,
    scenario_id: int,
    _: SameOrigin,
    context: ManageGroup,
) -> Response:
    """Store the image of a scenario of the group."""
    return await _save_image(request, locale, context, _owner_of(slug=slug), scenario_id)


@router.post(
    "/organisations/{org}/closure/scenarios/{scenario_id}/image/remove",
    response_class=HTMLResponse,
)
async def organisation_remove_image(
    request: Request, org: str, scenario_id: int, _: SameOrigin, context: ManageOrganisation
) -> Response:
    """Remove the image of an organisation scenario."""
    return await _remove_image(request, context, _owner_of(org=org), scenario_id)


@router.post(
    "/groups/{slug}/closure/scenarios/{scenario_id}/image/remove", response_class=HTMLResponse
)
async def group_remove_image(
    request: Request, slug: str, scenario_id: int, _: SameOrigin, context: ManageGroup
) -> Response:
    """Remove the image of a scenario of the group."""
    return await _remove_image(request, context, _owner_of(slug=slug), scenario_id)


@router.get("/organisations/{org}/closure/scenarios/{scenario_id}/image")
async def organisation_image(
    request: Request, org: str, scenario_id: int, context: ManageOrganisation
) -> Response:
    """Serve the image of an organisation scenario to its admins."""
    return await _image(request, context, _owner_of(org=org), scenario_id)


@router.get("/groups/{slug}/closure/scenarios/{scenario_id}/image")
async def group_image(
    request: Request, slug: str, scenario_id: int, context: ManageGroup
) -> Response:
    """Serve the image of a scenario the group sees (its own or the organisation's)."""
    return await _image(request, context, _owner_of(slug=slug), scenario_id)
