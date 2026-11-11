"""Admin routes for the skill catalogue of an organisation (ADR 0121 points 5 and 11).

Every route depends on exactly one ``RequirePermission(skills.manage)`` (organisation admin and
superuser), reading included; the pages are ``fresh``. Changes are POST forms with the Fetch
Metadata check, answered with a redirect and a confirmation; ``SkillService`` authorises again,
validates and audits. A skill of another organisation answers like a missing one.

    GET  /admin/organisations/{org}/skills                       skills.manage  the catalogue
    POST /admin/organisations/{org}/skills                       skills.manage  add a skill
    GET  /admin/organisations/{org}/skills/{skill_id}            skills.manage  edit the labels
    POST /admin/organisations/{org}/skills/{skill_id}            skills.manage  save the labels
    GET  /admin/organisations/{org}/skills/{skill_id}/archive    skills.manage  what archiving does
    POST /admin/organisations/{org}/skills/{skill_id}/archive    skills.manage  archive it

The list has the archived skills behind ``?archived=1``. There is no delete: a skill that
anything refers to stays. The code is fixed after creation.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, done_message, read_form, redirect_done
from app.admin.pages import (
    Locale,
    SameOrigin,
    organisation_header,
    page,
    with_manual,
)
from app.admin.skill_inputs import other_label, raise_unknown, refuse, skill_label
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.skill_service import SkillCatalogue, SkillLine, SkillService
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SKILLS_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every page and change of an organisation's skills."""

ARCHIVED_PARAM = "archived"
"""``?archived=1`` lists the archived skills, read-only."""


def skill_service(request: Request) -> SkillService:
    """Return the application's skill service."""
    service: SkillService = request.app.state.skill_service
    return service


def _url(org: str) -> str:
    """Return the skill catalogue of organisation ``org``."""
    return f"/admin/organisations/{org}/skills"


@dataclass(frozen=True)
class SkillRowView:
    """One skill of the catalogue as the list and the confirmation page show it."""

    id: int
    code: str
    label: str
    other_label: str
    staff: int
    pools: int
    flows: int
    archived: bool


def _row(line: SkillLine, locale: RequestLocale) -> SkillRowView:
    """Translate a skill with its use for the templates."""
    skill = line.skill
    return SkillRowView(
        skill.id or 0,
        skill.code,
        skill_label(skill, locale.code),
        other_label(skill, locale.code),
        line.usage.staff,
        line.usage.pools,
        line.usage.flows,
        skill.archived,
    )


def _label_fields(form: FormData, locale: RequestLocale, line: SkillLine | None) -> list[Field]:
    """Return the Dutch and English label inputs, filled from the form or the stored skill."""
    _ = locale.gettext
    stored = line.skill.labels if line else {}
    texts = {"nl": _("admin.skills.label_nl"), "en": _("admin.skills.label_en")}
    return [
        Field(
            f"label_{code}",
            text,
            value=form.values.get(f"label_{code}", stored.get(code, "")),
            error=form.error_for(f"label_{code}"),
            attributes={"maxlength": "80", "autocomplete": "off"},
        )
        for code, text in texts.items()
    ]


def _code_field(form: FormData, locale: RequestLocale, line: SkillLine | None) -> Field:
    """Return the code input: typed for a new skill, shown read-only for a stored one."""
    _ = locale.gettext
    if line is not None:
        return Field(
            "skill_code",
            _("admin.skills.code"),
            value=line.skill.code,
            readonly=True,
            hint=_("admin.skills.code_fixed"),
        )
    return Field(
        "skill_code",
        _("admin.skills.code"),
        value=form.text("skill_code"),
        hint=_("admin.skills.code_hint"),
        error=form.error_for("skill_code"),
        attributes={"maxlength": "40", "spellcheck": "false", "autocapitalize": "none"},
    )


async def _list_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the catalogue, with the form that adds a skill."""
    archived = request.query_params.get(ARCHIVED_PARAM) == "1"
    try:
        found: SkillCatalogue = await skill_service(request).catalogue(
            context.principal, org, archived=archived
        )
    except DomainError as error:
        raise_unknown(error)
    fields = with_manual(
        request, locale, [_code_field(form, locale, None), *_label_fields(form, locale, None)]
    )
    response = page(
        request,
        locale,
        "admin/organisation_skills.html",
        context.principal,
        {
            "organisation": found.organisation,
            "org_header": organisation_header(
                request, locale, context.principal, found.organisation.name, found.organisation.slug
            ),
            "rows": [_row(line, locale) for line in found.lines],
            "archived": found.archived,
            "fields": fields,
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
    skill_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form that changes the labels of one skill."""
    try:
        found = await skill_service(request).skill(context.principal, org, skill_id)
    except DomainError as error:
        raise_unknown(error)
    line, organisation = found.line, found.organisation
    fields = with_manual(
        request, locale, [_code_field(form, locale, line), *_label_fields(form, locale, line)]
    )
    response = page(
        request,
        locale,
        "admin/organisation_skill.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "row": _row(line, locale),
            "fields": fields,
            "errors": form.errors,
        },
    )
    response.status_code = status
    return response


async def _archive_page(
    request: Request, locale: RequestLocale, context: AdminContext, org: str, skill_id: int
) -> HTMLResponse:
    """Render the page that says what archiving a skill does, with the button."""
    try:
        found = await skill_service(request).skill(context.principal, org, skill_id)
    except DomainError as error:
        raise_unknown(error)
    line, organisation = found.line, found.organisation
    return page(
        request,
        locale,
        "admin/organisation_skill_archive.html",
        context.principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, context.principal, organisation.name, organisation.slug
            ),
            "row": _row(line, locale),
            "errors": [],
        },
    )


@router.get("/organisations/{org}/skills", response_class=HTMLResponse)
async def skills(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the skill catalogue of one organisation."""
    return await _list_page(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/skills", response_class=HTMLResponse)
async def add_skill(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Add a skill with a code and a label in Dutch and English."""
    form = await read_form(request, locale)
    try:
        await skill_service(request).create(
            context.principal,
            org,
            form.text("skill_code"),
            form.text("label_nl"),
            form.text("label_en"),
        )
    except DomainError as error:
        refuse(form, error, locale, "skill_code")
        return await _list_page(request, locale, context, org, form, 422)
    return redirect_done(_url(org), "skill_created")


@router.get("/organisations/{org}/skills/{skill_id}", response_class=HTMLResponse)
async def skill_form(
    request: Request, locale: Locale, org: str, skill_id: int, context: Manage
) -> Response:
    """Show the labels of one skill; the code is shown but fixed."""
    return await _edit_page(request, locale, context, org, skill_id, FormData({}, locale.gettext))


@router.post("/organisations/{org}/skills/{skill_id}", response_class=HTMLResponse)
async def save_skill(
    request: Request, locale: Locale, org: str, skill_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Save the labels of a skill."""
    form = await read_form(request, locale)
    try:
        await skill_service(request).relabel(
            context.principal, org, skill_id, form.text("label_nl"), form.text("label_en")
        )
    except DomainError as error:
        refuse(form, error, locale, "label_nl")
        return await _edit_page(request, locale, context, org, skill_id, form, 422)
    return redirect_done(_url(org), "skill_saved")


@router.get("/organisations/{org}/skills/{skill_id}/archive", response_class=HTMLResponse)
async def archive_form(
    request: Request, locale: Locale, org: str, skill_id: int, context: Manage
) -> Response:
    """Show what archiving a skill does and ask for the confirmation."""
    return await _archive_page(request, locale, context, org, skill_id)


@router.post("/organisations/{org}/skills/{skill_id}/archive", response_class=HTMLResponse)
async def archive_skill(
    request: Request, locale: Locale, org: str, skill_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Archive a skill; nothing that has it loses it."""
    try:
        await skill_service(request).archive(context.principal, org, skill_id)
    except DomainError as error:
        raise_unknown(error)
    return redirect_done(_url(org), "skill_archived")
