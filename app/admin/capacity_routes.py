"""Admin routes for the capacity pools of an organisation (ADR 0121 points 6 and 11).

The pools are shown on the page *Skills and capacity* (``app.admin.skills_capacity_page``, route
``app.admin.skill_routes``), read under ``organisation.view``. Every change, and the page of one
pool, depends on exactly one ``RequirePermission(capacity.manage)`` (organisation admin and
superuser) with a recent login. Changes are POST forms with the Fetch Metadata check, answered
with a redirect and a confirmation; ``CapacityService`` authorises again, validates and audits.
A pool of another organisation, an archived one and a pool of an existing service (legacy) have
no page and no change here and answer like a missing one (404).

    GET  /admin/organisations/{org}/capacity                       organisation.view  moved: 308
    POST /admin/organisations/{org}/capacity                       capacity.manage    add a pool
    GET  /admin/organisations/{org}/capacity/{pool_id}             capacity.manage    edit a pool
    POST /admin/organisations/{org}/capacity/{pool_id}             capacity.manage    save it
    GET  /admin/organisations/{org}/capacity/{pool_id}/archive     capacity.manage    confirmation
    POST /admin/organisations/{org}/capacity/{pool_id}/archive     capacity.manage    archive it

The list has the archived pools behind ``?pools=archived``. How many members of staff a counted
pool has per time block is not set here yet; it follows once flows use the pool.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, capacity_service, skill_service
from app.admin.capacity_views import (
    CHOICE_FIELD,
    name_field,
    pool_choices,
    pool_code_field,
    pool_row,
)
from app.admin.forms import FormData, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, org_page, with_manual
from app.admin.skill_inputs import chosen_ids, raise_unknown, refuse
from app.admin.skills_capacity_page import POOLS_ANCHOR, skills_capacity_page, url
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.ORGANISATION_VIEW, organisation_param="org")),
]
"""The permission to follow the old address of the capacity pools to their new home."""

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CAPACITY_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every change of an organisation's capacity pools and of one pool's page."""


async def _edit_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form of one counted pool; anything else is a 404 (``raise_unknown``)."""
    try:
        found = await capacity_service(request).edit(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    return org_page(
        request,
        locale,
        context.principal,
        found.organisation,
        "admin/organisation_pool.html",
        {
            "row": pool_row(found.line, locale),
            "fields": with_manual(
                request,
                locale,
                [pool_code_field(form, locale, found.line), name_field(form, locale, found.line)],
            ),
            "choices": pool_choices(
                found.choices, found.line.pool.guaranteed_skill_ids, form, locale
            ),
            "choice_field": CHOICE_FIELD,
            "choice_error": form.error_for(CHOICE_FIELD),
            "errors": form.errors,
            "back": f"{url(org)}#{POOLS_ANCHOR}",
        },
        status=status,
    )


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
        found = await capacity_service(request).page(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    return org_page(
        request,
        locale,
        context.principal,
        found.organisation,
        "admin/organisation_pool_archive.html",
        {
            "row": pool_row(found.line, locale),
            "errors": form.errors,
            "back": f"{url(org)}#{POOLS_ANCHOR}",
        },
        status=status,
    )


@router.get("/organisations/{org}/capacity")
async def pools(org: str, _: View) -> RedirectResponse:
    """Send the old address of the capacity pools to their section of *Skills and capacity*."""
    return RedirectResponse(f"{url(org)}#{POOLS_ANCHOR}", status_code=308)


@router.post("/organisations/{org}/capacity", response_class=HTMLResponse)
async def add_pool(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Add a counted pool with a code, a name and the skills each of its units holds."""
    form = await read_form(request, locale)
    try:
        await capacity_service(request).create(
            context.principal,
            org,
            form.text("pool_code"),
            form.text("pool_name"),
            chosen_ids(form, skill_service(request).max_skills),
        )
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD)
        return await skills_capacity_page(
            request, locale, context.principal, org, pool_form=form, status=422
        )
    return redirect_done(url(org), "pool_created", anchor=POOLS_ANCHOR)


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
            context.principal,
            org,
            pool_id,
            form.text("pool_name"),
            chosen_ids(form, skill_service(request).max_skills),
        )
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD)
        return await _edit_page(request, locale, context, org, pool_id, form, 422)
    return redirect_done(url(org), "pool_saved" if changed else "unchanged", anchor=POOLS_ANCHOR)


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
            found = await capacity_service(request).page(context.principal, org, pool_id)
            row = pool_row(found.line, locale)
            names = f"{', '.join(row.flows)} {row.hidden}".strip()
            message = locale.gettext("admin.capacity.error.in_use", flows=names)
            form.errors.append(FormError(None, message))
        return await _archive_page(request, locale, context, org, pool_id, form, 422)
    return redirect_done(url(org), "pool_archived", anchor=POOLS_ANCHOR)
