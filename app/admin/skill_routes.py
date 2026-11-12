"""Admin routes for the skill catalogue of an organisation (ADR 0121 points 5 and 11).

The catalogue is shown on the page *Skills and capacity* (``app.admin.skills_capacity_page``),
read under ``organisation.view``. Every change, and the page of one skill, depends on exactly one
``RequirePermission(skills.manage)`` (organisation admin and superuser) with a recent login.
Changes are POST forms with the Fetch Metadata check, answered with a redirect and a
confirmation; ``SkillService`` authorises again, validates and audits. A skill of another
organisation, and an archived one, answer like a missing one.

    GET  /admin/organisations/{org}/skills                       organisation.view  both lists
    POST /admin/organisations/{org}/skills                       skills.manage      add a skill
    GET  /admin/organisations/{org}/skills/{skill_id}            skills.manage      edit the labels
    POST /admin/organisations/{org}/skills/{skill_id}            skills.manage      save it
    GET  /admin/organisations/{org}/skills/{skill_id}/archive    skills.manage      confirmation
    POST /admin/organisations/{org}/skills/{skill_id}/archive    skills.manage      archive it

The page of one skill also carries its contact-center code (ADR 0121 step 4b): what the
organisation's contact-center flow tests for the skill; blank means not mapped.

The list has the archived skills behind ``?skills=archived``. There is no delete: a skill that
anything refers to stays. The code is fixed after creation.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, skill_service
from app.admin.forms import FormData, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, org_page, with_manual
from app.admin.skill_inputs import raise_unknown, refuse
from app.admin.skill_views import code_field, contact_center_field, label_fields, skill_row
from app.admin.skills_capacity_page import SKILLS_ANCHOR, skills_capacity_page, url
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.ORGANISATION_VIEW, organisation_param="org")),
]
"""The permission to read the page *Skills and capacity*."""

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SKILLS_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every change of an organisation's skills and of the page of one skill."""


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
    return org_page(
        request,
        locale,
        context.principal,
        found.organisation,
        "admin/organisation_skill.html",
        {
            "row": skill_row(found.line, locale),
            "fields": with_manual(
                request,
                locale,
                [
                    code_field(form, locale, found.line),
                    *label_fields(form, locale, found.line),
                    contact_center_field(form, locale, found.line),
                ],
            ),
            "errors": form.errors,
            "back": f"{url(org)}#{SKILLS_ANCHOR}",
        },
        status=status,
    )


@router.get("/organisations/{org}/skills", response_class=HTMLResponse)
async def skills(request: Request, locale: Locale, org: str, context: View) -> Response:
    """Show the skills and the capacity pools of one organisation."""
    return await skills_capacity_page(request, locale, context.principal, org)


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
        return await skills_capacity_page(
            request, locale, context.principal, org, skill_form=form, status=422
        )
    return redirect_done(url(org), "skill_created", anchor=SKILLS_ANCHOR)


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
        changed = await skill_service(request).relabel(
            context.principal,
            org,
            skill_id,
            form.text("label_nl"),
            form.text("label_en"),
            form.text("contact_center_code"),
        )
    except DomainError as error:
        refuse(form, error, locale, "label_nl")
        return await _edit_page(request, locale, context, org, skill_id, form, 422)
    return redirect_done(url(org), "skill_saved" if changed else "unchanged", anchor=SKILLS_ANCHOR)


@router.get("/organisations/{org}/skills/{skill_id}/archive", response_class=HTMLResponse)
async def archive_form(
    request: Request, locale: Locale, org: str, skill_id: int, context: Manage
) -> Response:
    """Show what archiving a skill does and ask for the confirmation."""
    try:
        found = await skill_service(request).skill(context.principal, org, skill_id)
    except DomainError as error:
        raise_unknown(error)
    return org_page(
        request,
        locale,
        context.principal,
        found.organisation,
        "admin/organisation_skill_archive.html",
        {
            "row": skill_row(found.line, locale),
            "errors": [],
            "back": f"{url(org)}#{SKILLS_ANCHOR}",
        },
    )


@router.post("/organisations/{org}/skills/{skill_id}/archive", response_class=HTMLResponse)
async def archive_skill(
    request: Request, locale: Locale, org: str, skill_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Archive a skill; nothing that has it loses it."""
    try:
        await skill_service(request).archive(context.principal, org, skill_id)
    except DomainError as error:
        raise_unknown(error)
    return redirect_done(url(org), "skill_archived", anchor=SKILLS_ANCHOR)
