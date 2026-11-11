"""Admin routes for the capacity pools of an organisation (ADR 0121 points 6 and 11).

Every route depends on exactly one ``RequirePermission(capacity.manage)`` (organisation admin
and superuser), reading included; the pages are ``fresh``. Changes are POST forms with the
Fetch Metadata check, answered with a redirect and a confirmation; ``CapacityService``
authorises again, validates and audits. A pool of another organisation answers like a missing
one. A pool of an existing service (legacy) is shown and never changed here.

    GET  /admin/organisations/{org}/capacity                       capacity.manage  the pools
    POST /admin/organisations/{org}/capacity                       capacity.manage  add a pool
    GET  /admin/organisations/{org}/capacity/{pool_id}             capacity.manage  edit a pool
    POST /admin/organisations/{org}/capacity/{pool_id}             capacity.manage  save it
    GET  /admin/organisations/{org}/capacity/{pool_id}/archive     capacity.manage  confirmation
    POST /admin/organisations/{org}/capacity/{pool_id}/archive     capacity.manage  archive it

The list has the archived pools behind ``?archived=1``. How many members of staff a counted
pool has per time block is not set here yet; it follows once flows use the pool.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, done_message, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, organisation_header, page, with_manual
from app.admin.skill_inputs import (
    SkillChoice,
    choices,
    chosen_ids,
    raise_unknown,
    refuse,
    skill_label,
)
from app.domain.authorization import Permission
from app.domain.capacity import PoolMode
from app.domain.capacity_service import CapacityService, FlowRef, PoolForm, PoolLine
from app.domain.errors import DomainError, ErrorCode
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CAPACITY_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every page and change of an organisation's capacity pools."""

ARCHIVED_PARAM = "archived"
"""``?archived=1`` lists the archived pools, read-only."""

CHOICE_FIELD = "guaranteed_skill_ids"
"""The id of the group of skill checkboxes, where an error about the choice links to."""


def capacity_service(request: Request) -> CapacityService:
    """Return the application's capacity service."""
    service: CapacityService = request.app.state.capacity_service
    return service


def _url(org: str) -> str:
    """Return the capacity pools of organisation ``org``."""
    return f"/admin/organisations/{org}/capacity"


@dataclass(frozen=True)
class PoolRowView:
    """One pool as the list, the form and the confirmation page show it."""

    id: int
    name: str
    legacy: bool
    tag: str
    skills: tuple[str, ...]
    flows: tuple[str, ...]
    hidden: str
    archived: bool


def flow_name(flow: FlowRef, locale: str) -> str:
    """Return a flow as ``label · service`` in the admin's language (Dutch when it has none)."""
    label = flow.labels.get(locale) or flow.labels.get("nl") or next(iter(flow.labels.values()))
    return f"{label} · {flow.service}" if flow.service else label


def hidden_flows(count: int, locale: RequestLocale) -> str:
    """Return "and N flows of other services" for ``count`` flows the viewer may not see."""
    _ = locale.gettext
    if count == 0:
        return ""
    key = "admin.capacity.flows_hidden_one" if count == 1 else "admin.capacity.flows_hidden"
    return _(key, count=count)


def _tag(line: PoolLine, locale: RequestLocale) -> str:
    """Return the mode tag: existing (legacy), or own and shared for a counted pool."""
    _ = locale.gettext
    if line.pool.mode is PoolMode.LEGACY_GROUP:
        return _("admin.capacity.mode.legacy")
    return _("admin.capacity.mode.shared" if line.shared else "admin.capacity.mode.own")


def _row(line: PoolLine, locale: RequestLocale) -> PoolRowView:
    """Translate a pool with its skills and flows for the templates."""
    return PoolRowView(
        line.pool.id or 0,
        line.pool.name,
        line.pool.mode is PoolMode.LEGACY_GROUP,
        _tag(line, locale),
        tuple(skill_label(s, locale.code) for s in line.skills),
        tuple(flow_name(f, locale.code) for f in line.flows),
        hidden_flows(line.hidden, locale),
        line.pool.archived_at is not None,
    )


def _name_field(form: FormData, locale: RequestLocale, line: PoolLine | None) -> Field:
    """Return the name input, filled from the form or the stored pool."""
    _ = locale.gettext
    stored = line.pool.name if line else ""
    return Field(
        "pool_name",
        _("admin.capacity.name"),
        value=form.values.get("pool_name", stored),
        error=form.error_for("pool_name"),
        attributes={"maxlength": "80", "autocomplete": "off"},
    )


def _choices(found: PoolForm, form: FormData, locale: RequestLocale) -> list[SkillChoice]:
    """Return the skills to choose from; the submitted ticks win over the stored ones."""
    stored = found.line.pool.guaranteed_skill_ids if found.line else frozenset[int]()
    return choices(found.choices, chosen_ids(form) if form.values else stored, locale.code)


async def _list_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the pools, with the form that adds a counted pool."""
    archived = request.query_params.get(ARCHIVED_PARAM) == "1"
    service = capacity_service(request)
    try:
        found = await service.overview(context.principal, org, archived=archived)
        adding = await service.form(context.principal, org, None)
    except DomainError as error:
        raise_unknown(error)
    organisation = found.organisation
    response = page(
        request,
        locale,
        "admin/organisation_capacity.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "rows": [_row(line, locale) for line in found.lines],
            "archived": found.archived,
            "name_field": with_manual(request, locale, [_name_field(form, locale, None)])[0],
            "choices": _choices(adding, form, locale),
            "choice_field": CHOICE_FIELD,
            "choice_error": form.error_for(CHOICE_FIELD),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


async def _edit_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form of one counted pool."""
    try:
        found = await capacity_service(request).form(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    line = found.line
    if line is None or line.pool.mode is not PoolMode.COUNTED:
        raise_unknown(DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"))
    response = page(
        request,
        locale,
        "admin/organisation_pool.html",
        context.principal,
        {
            "organisation": found.organisation,
            "org_header": organisation_header(
                request, locale, context.principal, found.organisation.name, found.organisation.slug
            ),
            "row": _row(line, locale),
            "name_field": with_manual(request, locale, [_name_field(form, locale, line)])[0],
            "choices": _choices(found, form, locale),
            "choice_field": CHOICE_FIELD,
            "choice_error": form.error_for(CHOICE_FIELD),
            "errors": form.errors,
        },
    )
    response.status_code = status
    return response


async def _archive_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render what archiving a pool does, with the flows that stop it, and the button."""
    try:
        found = await capacity_service(request).form(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    line = found.line
    if line is None or line.pool.mode is not PoolMode.COUNTED:
        raise_unknown(DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id"))
    response = page(
        request,
        locale,
        "admin/organisation_pool_archive.html",
        context.principal,
        {
            "organisation": found.organisation,
            "org_header": organisation_header(
                request, locale, context.principal, found.organisation.name, found.organisation.slug
            ),
            "row": _row(line, locale),
            "errors": form.errors,
        },
    )
    response.status_code = status
    return response


@router.get("/organisations/{org}/capacity", response_class=HTMLResponse)
async def pools(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the capacity pools of one organisation."""
    return await _list_page(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/capacity", response_class=HTMLResponse)
async def add_pool(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Add a counted pool with a name and the skills each of its units holds."""
    form = await read_form(request, locale)
    try:
        await capacity_service(request).create(
            context.principal, org, form.text("pool_name"), chosen_ids(form)
        )
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD)
        return await _list_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "pool_created")


@router.get("/organisations/{org}/capacity/{pool_id}", response_class=HTMLResponse)
async def pool_form(
    request: Request, locale: Locale, org: str, pool_id: int, context: Manage
) -> Response:
    """Show the name and guaranteed skills of one counted pool."""
    return await _edit_page(request, locale, context, org, pool_id, FormData({}, locale.gettext))


@router.post("/organisations/{org}/capacity/{pool_id}", response_class=HTMLResponse)
async def save_pool(
    request: Request, locale: Locale, org: str, pool_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Save the name and guaranteed skills of a counted pool."""
    form = await read_form(request, locale)
    try:
        changed = await capacity_service(request).update(
            context.principal, org, pool_id, form.text("pool_name"), chosen_ids(form)
        )
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD)
        return await _edit_page(request, locale, context, org, pool_id, form, 422)
    return redirect_done(_url(org), "pool_saved" if changed else "unchanged")


@router.get("/organisations/{org}/capacity/{pool_id}/archive", response_class=HTMLResponse)
async def archive_form(
    request: Request, locale: Locale, org: str, pool_id: int, context: Manage
) -> Response:
    """Show what archiving a pool does and ask for the confirmation."""
    return await _archive_page(request, locale, context, org, pool_id, FormData({}, locale.gettext))


@router.post("/organisations/{org}/capacity/{pool_id}/archive", response_class=HTMLResponse)
async def archive_pool(
    request: Request, locale: Locale, org: str, pool_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Archive a counted pool; refused, with the flows named, while a flow draws on it."""
    form = FormData({}, locale.gettext)
    try:
        await capacity_service(request).archive(context.principal, org, pool_id)
    except DomainError as error:
        if error.code is not ErrorCode.CONFIGURATION_OBJECT_IN_USE:
            refuse(form, error, locale, CHOICE_FIELD)
        else:
            row = _row(
                (await capacity_service(request).pool(context.principal, org, pool_id)), locale
            )
            names = f"{', '.join(row.flows)} {row.hidden}".strip()
            message = locale.gettext("admin.capacity.error.in_use", flows=names)
            form.errors.append(FormError(None, message))
        return await _archive_page(request, locale, context, org, pool_id, form, 422)
    return redirect_done(_url(org), "pool_archived")
