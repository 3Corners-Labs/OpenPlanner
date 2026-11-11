"""The page *Skills and capacity* of an organisation, rendered for every route that shows it.

One screen holds both lists (ADR 0121 point 11, decision S2). It is read under
``organisation.view``; the add forms and the edit and archive links appear only for a viewer who
holds ``skills.manage`` respectively ``capacity.manage`` there. The list route, and the add
routes when they refuse a form, render it through ``skills_capacity_page``, so a refusal
re-shows the whole page with the error on its own form and named by its section.
"""

from fastapi import Request
from starlette.responses import HTMLResponse

from app.admin.authz import capacity_service, skill_service
from app.admin.capacity_views import CHOICE_FIELD, name_field, pool_choices, pool_row
from app.admin.forms import FormData, done_message
from app.admin.pages import archived_requested, org_page, with_manual
from app.admin.skill_inputs import POOL_LIMIT_NOTICE, SKILL_LIMIT_NOTICE, raise_unknown
from app.admin.skill_views import code_field, label_fields, skill_row
from app.domain.authorization import Principal
from app.domain.errors import DomainError
from app.web.errors import FormError
from app.web.locale import RequestLocale

SKILLS_FILTER = "skills"
"""``?skills=archived`` lists the archived skills."""

POOLS_FILTER = "pools"
"""``?pools=archived`` lists the archived pools."""

SKILLS_ANCHOR = "skills"
"""The id of the skills section."""

POOLS_ANCHOR = "capacity"
"""The id of the capacity pools section."""


def url(org: str) -> str:
    """Return the page *Skills and capacity* of organisation ``org``."""
    return f"/admin/organisations/{org}/skills"


def _named(errors: list[FormError], section: str, locale: RequestLocale) -> list[FormError]:
    """Return ``errors`` with the section they belong to in front of each message."""
    _ = locale.gettext
    return [
        FormError(e.field, _("admin.skills_capacity.error_in", section=section, message=e.message))
        for e in errors
    ]


async def skills_capacity_page(
    request: Request,
    locale: RequestLocale,
    principal: Principal,
    org: str,
    *,
    skill_form: FormData | None = None,
    pool_form: FormData | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render both lists of organisation ``org``; a refused form re-shows its typed values.

    ``skill_form`` and ``pool_form`` are the add form that was refused, if any, with its errors.

    Raises:
        AccessDenied: The viewer lacks ``organisation.view``.
        RejectedRequest: 404 for an unknown organisation.
    """
    _ = locale.gettext
    skill_form = skill_form or FormData({}, _)
    pool_form = pool_form or FormData({}, _)
    skills_archived = archived_requested(request, SKILLS_FILTER)
    pools_archived = archived_requested(request, POOLS_FILTER)
    skills, pools = skill_service(request), capacity_service(request)
    try:
        found_skills = await skills.catalogue(principal, org, archived=skills_archived)
        found_pools = await pools.overview(principal, org, archived=pools_archived)
        adding = (
            await pools.form(principal, org)
            if found_pools.editable and found_pools.can_add and not pools_archived
            else None
        )
    except DomainError as error:
        raise_unknown(error)
    showing_skill_form = found_skills.editable and found_skills.can_add and not skills_archived
    errors = [
        *_named(skill_form.errors, _("admin.skills.title"), locale),
        *_named(pool_form.errors, _("admin.capacity.title"), locale),
    ]
    return org_page(
        request,
        locale,
        principal,
        found_skills.organisation,
        "admin/organisation_skills.html",
        {
            "skills_archived": skills_archived,
            "skills_editable": found_skills.editable,
            "skills_limit": found_skills.limit,
            "skills_can_add": showing_skill_form,
            "limit_reached": found_skills.editable and not found_skills.can_add,
            "skill_rows": [skill_row(line, locale) for line in found_skills.lines],
            "skill_fields": with_manual(
                request,
                locale,
                [code_field(skill_form, locale, None), *label_fields(skill_form, locale, None)],
            )
            if showing_skill_form
            else [],
            "pools_archived": pools_archived,
            "pools_editable": found_pools.editable,
            "pools_limit": found_pools.limit,
            "pools_can_add": adding is not None,
            "pools_limit_reached": found_pools.editable and not found_pools.can_add,
            "pool_rows": [pool_row(line, locale) for line in found_pools.lines],
            "pool_name_field": with_manual(request, locale, [name_field(pool_form, locale, None)])[
                0
            ]
            if adding is not None
            else None,
            "pool_choices": pool_choices(adding.choices, frozenset(), pool_form, locale)
            if adding is not None
            else [],
            "choice_field": CHOICE_FIELD,
            "choice_error": pool_form.error_for(CHOICE_FIELD),
            "errors": errors,
            "done": done_message(request, _),
            "skill_limit_notice": SKILL_LIMIT_NOTICE,
            "pool_limit_notice": POOL_LIMIT_NOTICE,
            "skills_anchor": SKILLS_ANCHOR,
            "pools_anchor": POOLS_ANCHOR,
        },
        status=status,
    )
