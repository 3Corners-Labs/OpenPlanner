"""Granting skills to a person on the staff page (ADR 0121 points 5 and 11).

    POST /admin/staff/{member_id}/skills/{org}   skills.manage, fresh login

The form is the section "Skills" of the person's page (``app.admin.staff_routes``): the skills
the person holds in one organisation, as checkboxes. The route depends on ``skills.manage`` in
that organisation; ``SkillService`` checks the matrix again, that the viewer may see the person
(``staff.view``), that the person belongs to the organisation for every skill that is added,
and audits the change with skill codes and the person's number, never a name. A person the
viewer may not see, or an organisation they have no right in, answers like an unknown address.
A change applies from the next request that is picked up or started.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.skill_inputs import chosen_ids, refuse
from app.admin.skill_routes import skill_service
from app.admin.staff_routes import member_page
from app.domain.authorization import Permission
from app.domain.errors import DomainError

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SKILLS_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of the skills of a person in one organisation."""


@router.post("/staff/{member_id}/skills/{org}", response_model=None)
async def save_staff_skills(
    request: Request, locale: Locale, member_id: int, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Replace the skills the person holds in ``org``; their skills elsewhere stay."""
    form = await read_form(request, locale)
    try:
        changed = await skill_service(request).grant(
            context.principal, org, member_id, chosen_ids(form)
        )
    except DomainError as error:
        refuse(form, error, locale, f"skills-{org}")
        return await member_page(
            request, locale, context, member_id, form=form, error_org=org, status=422
        )
    return redirect_done(
        f"/admin/staff/{member_id}",
        "staff_skills_saved" if changed else "unchanged",
        anchor="skills",
    )
