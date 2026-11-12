"""Admin routes for the staffing of a counted capacity pool (ADR 0122, step 4).

The pages are sub-pages of *Skills and capacity* (organisation, Overview), next to the pool's own
page. Every route, reading included, depends on exactly one ``RequirePermission(capacity.manage)``
(organisation admin and superuser) with a recent login (decision P2: only ``capacity.manage``
changes a pool's staffing). Changes are POST forms with the Fetch Metadata check, answered with a
redirect and a confirmation; ``PoolPlanningService`` authorises again, validates the whole
schedule and audits. A pool of another organisation, an archived one and a legacy pool, and a
block or an exception the pool does not have, answer 404.

    GET  /admin/organisations/{org}/capacity/{pool}/staffing               week and shortfalls
    POST /admin/organisations/{org}/capacity/{pool}/staffing               add a block
    POST /admin/organisations/{org}/capacity/{pool}/timezone               set the time zone
    GET  /admin/organisations/{org}/capacity/{pool}/staffing/{id}          change-block form
    POST /admin/organisations/{org}/capacity/{pool}/staffing/{id}          change a block
    GET  /admin/organisations/{org}/capacity/{pool}/staffing/{id}/split    split-block form
    POST /admin/organisations/{org}/capacity/{pool}/staffing/{id}/split    split a block in two
    POST /admin/organisations/{org}/capacity/{pool}/staffing/{id}/delete   remove a block
    GET  /admin/organisations/{org}/capacity/{pool}/exceptions             exceptions, add form
    POST /admin/organisations/{org}/capacity/{pool}/exceptions             add an exception
    GET  /admin/organisations/{org}/capacity/{pool}/exceptions/{id}        change-exception form
    POST /admin/organisations/{org}/capacity/{pool}/exceptions/{id}        change an exception
    POST /admin/organisations/{org}/capacity/{pool}/exceptions/{id}/delete remove an exception

A pool has no blocks to enter until it has a time zone. No booking path reads a pool's staffing
yet; the page shows the shortfalls its own bookings would cause, of which there are none before
a flow books on the pool.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, pool_planning_service
from app.admin.forms import Field, FormData, done_message, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, org_page
from app.admin.pool_staffing_views import TIMEZONE_FIELD, shortfall_list, timezone_field
from app.admin.skill_inputs import raise_unknown, refuse
from app.admin.skills_capacity_page import POOLS_ANCHOR
from app.admin.skills_capacity_page import url as skills_capacity_url
from app.admin.staffing_views import (
    POOL_HINTS,
    WEEKDAYS,
    block_description,
    exception_fields,
    exception_views,
    hours_fields,
    override_values,
    read_override,
    read_rule,
    rule_values,
    split_fields,
    week,
)
from app.domain.authorization import Permission
from app.domain.capacity import CapacityPool
from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_planning import override_version, rule_version
from app.domain.scheduling import Organisation
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CAPACITY_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of every page and change of a pool's staffing."""


def _pool_path(org: str, pool_id: int) -> str:
    """Return the address of the pool's own page; the staffing pages hang under it."""
    return f"/admin/organisations/{org}/capacity/{pool_id}"


def _staffing_path(org: str, pool_id: int) -> str:
    """Return the address of the pool's staffing page."""
    return f"{_pool_path(org, pool_id)}/staffing"


def _exceptions_path(org: str, pool_id: int) -> str:
    """Return the address of the pool's exceptions page."""
    return f"{_pool_path(org, pool_id)}/exceptions"


def _refused(form: FormData, error: DomainError, locale: RequestLocale) -> None:
    """Add the form error of a refusal; a missing pool, block or exception raises the 404."""
    refuse(form, error, locale, "")


def _changed(error: DomainError) -> bool:
    """Whether the refusal says the row changed since the form was loaded."""
    return error.code is ErrorCode.POOL_STAFFING_CHANGED


def _notice(locale: RequestLocale, error: DomainError) -> FormData:
    """Return an empty form that carries the refusal ``error`` as its error summary."""
    form = FormData({}, locale.gettext)
    form.refusal(error)
    return form


def _page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    organisation: Organisation,
    pool: CapacityPool,
    template: str,
    extra: dict[str, object],
    status: int,
) -> HTMLResponse:
    """Render a staffing page: the organisation's header, the pool's name and links, ``extra``.

    The organisation and the pool come from the use case that read the page's own data, so a page
    view is one authorisation and one read.
    """
    org, pool_id = organisation.slug, pool.id or 0
    return org_page(
        request,
        locale,
        context.principal,
        organisation,
        template,
        {
            "row": pool,
            "list_path": f"{skills_capacity_url(org)}#{POOLS_ANCHOR}",
            "pool_path": _pool_path(org, pool_id),
            "staffing_path": _staffing_path(org, pool_id),
            "exceptions_path": _exceptions_path(org, pool_id),
            "subject_choices": [],
            "done": done_message(request, locale.gettext),
            **extra,
        },
        status=status,
    )


# --- time zone and weekly blocks -------------------------------------------------------------


async def _staffing_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    *,
    block: FormData | None = None,
    zone: FormData | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the time zone, the shortfalls, the week and the add-block form of a pool."""
    _ = locale.gettext
    zone = zone or FormData({}, _)
    if block is None:
        weekday = request.query_params.get("weekday", "")
        block = FormData({"weekday": weekday} if weekday in WEEKDAYS else {}, _)
    try:
        found = await pool_planning_service(request).staffing(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    return _page(
        request,
        locale,
        context,
        found.organisation,
        found.pool,
        "admin/organisation_pool_staffing.html",
        {
            "timezone": found.timezone,
            "timezone_field": timezone_field(zone, locale, found.timezone),
            "week": week(found.rules, locale, rule_version),
            "staffing_base": _staffing_path(org, pool_id),
            "fields": hours_fields(block, locale, POOL_HINTS),
            "shortfalls": shortfall_list(
                found.shortfalls, found.shortfall_total, found.timezone, locale
            )
            if found.timezone
            else None,
            "horizon_days": found.horizon_days,
            "errors": [*zone.errors, *block.errors],
        },
        status,
    )


@router.get("/organisations/{org}/capacity/{pool_id}/staffing", response_class=HTMLResponse)
async def staffing(
    request: Request, locale: Locale, org: str, pool_id: int, context: Manage
) -> HTMLResponse:
    """Show a pool's time zone, weekly blocks and shortfalls; ``?weekday=`` picks the add day."""
    return await _staffing_response(request, locale, context, org, pool_id)


@router.post("/organisations/{org}/capacity/{pool_id}/timezone", response_class=HTMLResponse)
async def set_timezone(
    request: Request, locale: Locale, org: str, pool_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Set the time zone the pool's blocks are read in."""
    form = await read_form(request, locale)
    try:
        changed = await pool_planning_service(request).set_timezone(
            context.principal, org, pool_id, form.text(TIMEZONE_FIELD)
        )
    except DomainError as error:
        _refused(form, error, locale)
        return await _staffing_response(
            request, locale, context, org, pool_id, zone=form, status=422
        )
    return redirect_done(_staffing_path(org, pool_id), "saved" if changed else "unchanged")


@router.post("/organisations/{org}/capacity/{pool_id}/staffing", response_class=HTMLResponse)
async def add_block(
    request: Request, locale: Locale, org: str, pool_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Add a weekly block of the pool."""
    form = await read_form(request, locale)
    rule = read_rule(form)
    if rule is not None:
        try:
            await pool_planning_service(request).add_rule(context.principal, org, pool_id, rule)
        except DomainError as error:
            _refused(form, error, locale)
        else:
            return redirect_done(_staffing_path(org, pool_id), "added")
    return await _staffing_response(request, locale, context, org, pool_id, block=form, status=422)


def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    organisation: Organisation,
    pool: CapacityPool,
    *,
    template: str,
    heading: str,
    lead: str,
    action: str,
    button: str,
    cancel: str,
    fields: list[Field],
    form: FormData,
    expected: str,
    status: int = 200,
) -> HTMLResponse:
    """Render the one-form page of a change: a block, a split or an exception.

    ``expected`` is the version of the stored row the form was loaded from; the form sends it
    back and the use case refuses the change if the row is no longer that version.
    """
    return _page(
        request,
        locale,
        context,
        organisation,
        pool,
        template,
        {
            "heading": heading,
            "lead": lead,
            "action": action,
            "button": button,
            "cancel": cancel,
            "fields": fields,
            "expected": expected,
            "kind": form.text("kind") or "closed",
            "kind_error": form.error_for("kind"),
            "errors": form.errors,
        },
        status,
    )


BLOCK_FORM = "admin/organisation_pool_block_form.html"
EXCEPTION_FORM = "admin/organisation_pool_exception_form.html"


async def _block_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    rule_id: int,
    form: FormData,
    status: int = 200,
    conflict: DomainError | None = None,
) -> HTMLResponse:
    """Render the change form of one weekly block.

    After a ``conflict`` it shows the block as it is stored now, with the new version and the
    reason, so the admin looks at it and saves again.
    """
    _ = locale.gettext
    try:
        item = await pool_planning_service(request).rule(context.principal, org, pool_id, rule_id)
    except DomainError as error:
        raise_unknown(error)
    expected = form.text("expected") or item.version
    if not form.values:
        form = FormData(rule_values(item.rule), _)
    if conflict is not None:
        form, expected, status = FormData(rule_values(item.rule), _), item.version, 409
        form.refusal(conflict)
    return _form_page(
        request,
        locale,
        context,
        item.organisation,
        item.pool,
        template=BLOCK_FORM,
        heading=_("admin.hours.edit"),
        lead=_("admin.hours.edit_lead"),
        action=f"{_staffing_path(org, pool_id)}/{rule_id}",
        button=_("admin.save"),
        cancel=_staffing_path(org, pool_id),
        fields=hours_fields(form, locale, POOL_HINTS),
        form=form,
        expected=expected,
        status=status,
    )


@router.get(
    "/organisations/{org}/capacity/{pool_id}/staffing/{rule_id}", response_class=HTMLResponse
)
async def edit_block(
    request: Request, locale: Locale, org: str, pool_id: int, rule_id: int, context: Manage
) -> HTMLResponse:
    """Show the change form of a weekly block, pre-filled with its values."""
    form = FormData({}, locale.gettext)
    return await _block_page(request, locale, context, org, pool_id, rule_id, form)


@router.post(
    "/organisations/{org}/capacity/{pool_id}/staffing/{rule_id}", response_class=HTMLResponse
)
async def update_block(
    request: Request,
    locale: Locale,
    org: str,
    pool_id: int,
    rule_id: int,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Change a weekly block; existing appointments stay (decision P3)."""
    form = await read_form(request, locale)
    rule = read_rule(form)
    if rule is not None:
        try:
            changed = await pool_planning_service(request).update_rule(
                context.principal, org, pool_id, rule_id, rule, form.text("expected")
            )
        except DomainError as error:
            if _changed(error):
                return await _block_page(
                    request, locale, context, org, pool_id, rule_id, form, conflict=error
                )
            _refused(form, error, locale)
        else:
            return redirect_done(_staffing_path(org, pool_id), "saved" if changed else "unchanged")
    return await _block_page(request, locale, context, org, pool_id, rule_id, form, 422)


async def _split_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    rule_id: int,
    form: FormData,
    status: int = 200,
    conflict: DomainError | None = None,
) -> HTMLResponse:
    """Render the split form of one weekly block, described in words above the inputs."""
    _ = locale.gettext
    try:
        item = await pool_planning_service(request).rule(context.principal, org, pool_id, rule_id)
    except DomainError as error:
        raise_unknown(error)
    expected = form.text("expected") or item.version
    if conflict is not None:
        form, expected, status = FormData({}, _), item.version, 409
        form.refusal(conflict)
    return _form_page(
        request,
        locale,
        context,
        item.organisation,
        item.pool,
        template=BLOCK_FORM,
        heading=_("admin.hours.split_title"),
        lead=_("admin.hours.split_lead", block=block_description(item.rule, locale)),
        action=f"{_staffing_path(org, pool_id)}/{rule_id}/split",
        button=_("admin.hours.split_button"),
        cancel=_staffing_path(org, pool_id),
        fields=split_fields(form, locale, POOL_HINTS),
        form=form,
        expected=expected,
        status=status,
    )


@router.get(
    "/organisations/{org}/capacity/{pool_id}/staffing/{rule_id}/split",
    response_class=HTMLResponse,
)
async def split_block_form(
    request: Request, locale: Locale, org: str, pool_id: int, rule_id: int, context: Manage
) -> HTMLResponse:
    """Show the form that splits a weekly block in two (ADR 0027)."""
    form = FormData({}, locale.gettext)
    return await _split_page(request, locale, context, org, pool_id, rule_id, form)


@router.post(
    "/organisations/{org}/capacity/{pool_id}/staffing/{rule_id}/split",
    response_class=HTMLResponse,
)
async def split_block(
    request: Request,
    locale: Locale,
    org: str,
    pool_id: int,
    rule_id: int,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Split a weekly block in two touching blocks; the domain validates and audits."""
    form = await read_form(request, locale)
    at, capacity = form.clock("split_at"), form.integer("capacity")
    label = form.optional_text("label")
    if not form.errors and at is not None and capacity is not None:
        try:
            await pool_planning_service(request).split_rule(
                context.principal, org, pool_id, rule_id, at, capacity, label, form.text("expected")
            )
        except DomainError as error:
            if _changed(error):
                return await _split_page(
                    request, locale, context, org, pool_id, rule_id, form, conflict=error
                )
            _refused(form, error, locale)
        else:
            return redirect_done(_staffing_path(org, pool_id), "saved")
    return await _split_page(request, locale, context, org, pool_id, rule_id, form, 422)


@router.post(
    "/organisations/{org}/capacity/{pool_id}/staffing/{rule_id}/delete",
    response_class=HTMLResponse,
)
async def remove_block(
    request: Request,
    locale: Locale,
    org: str,
    pool_id: int,
    rule_id: int,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Remove a weekly block, if it is still the one the page showed."""
    form = await read_form(request, locale)
    try:
        await pool_planning_service(request).remove_rule(
            context.principal, org, pool_id, rule_id, form.text("expected")
        )
    except DomainError as error:
        if not _changed(error):
            raise_unknown(error)
        return await _staffing_response(
            request, locale, context, org, pool_id, block=_notice(locale, error), status=409
        )
    return redirect_done(_staffing_path(org, pool_id), "removed")


# --- exceptions ------------------------------------------------------------------------------


async def _exceptions_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the coming exceptions with the add-exception form."""
    try:
        found = await pool_planning_service(request).staffing(context.principal, org, pool_id)
    except DomainError as error:
        raise_unknown(error)
    return _page(
        request,
        locale,
        context,
        found.organisation,
        found.pool,
        "admin/organisation_pool_exceptions.html",
        {
            "timezone": found.timezone,
            "exceptions": exception_views(found.overrides, locale, override_version),
            "exceptions_base": _exceptions_path(org, pool_id),
            "fields": exception_fields(form, locale, POOL_HINTS),
            "kind": form.text("kind") or "closed",
            "kind_error": form.error_for("kind"),
            "errors": form.errors,
        },
        status,
    )


@router.get("/organisations/{org}/capacity/{pool_id}/exceptions", response_class=HTMLResponse)
async def exceptions(
    request: Request, locale: Locale, org: str, pool_id: int, context: Manage
) -> HTMLResponse:
    """Show the coming exceptions of a pool."""
    form = FormData({}, locale.gettext)
    return await _exceptions_response(request, locale, context, org, pool_id, form)


@router.post("/organisations/{org}/capacity/{pool_id}/exceptions", response_class=HTMLResponse)
async def add_exception(
    request: Request, locale: Locale, org: str, pool_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Close a date or give it its own blocks."""
    form = await read_form(request, locale)
    override = read_override(form)
    if override is not None:
        try:
            await pool_planning_service(request).add_override(
                context.principal, org, pool_id, override
            )
        except DomainError as error:
            _refused(form, error, locale)
        else:
            return redirect_done(_exceptions_path(org, pool_id), "added")
    return await _exceptions_response(request, locale, context, org, pool_id, form, 422)


async def _exception_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    pool_id: int,
    override_id: int,
    form: FormData,
    status: int = 200,
    conflict: DomainError | None = None,
) -> HTMLResponse:
    """Render the change form of one exception (after a ``conflict``: as it is stored now)."""
    _ = locale.gettext
    try:
        item = await pool_planning_service(request).override(
            context.principal, org, pool_id, override_id
        )
    except DomainError as error:
        raise_unknown(error)
    expected = form.text("expected") or item.version
    if not form.values:
        form = FormData(override_values(item.override), _)
    if conflict is not None:
        form, expected, status = FormData(override_values(item.override), _), item.version, 409
        form.refusal(conflict)
    return _form_page(
        request,
        locale,
        context,
        item.organisation,
        item.pool,
        template=EXCEPTION_FORM,
        heading=_("admin.exceptions.edit"),
        lead=_("admin.exceptions.edit_lead"),
        action=f"{_exceptions_path(org, pool_id)}/{override_id}",
        button=_("admin.save"),
        cancel=_exceptions_path(org, pool_id),
        fields=exception_fields(form, locale, POOL_HINTS),
        form=form,
        expected=expected,
        status=status,
    )


@router.get(
    "/organisations/{org}/capacity/{pool_id}/exceptions/{override_id}",
    response_class=HTMLResponse,
)
async def edit_exception(
    request: Request, locale: Locale, org: str, pool_id: int, override_id: int, context: Manage
) -> HTMLResponse:
    """Show the change form of an exception, pre-filled with its values."""
    form = FormData({}, locale.gettext)
    return await _exception_page(request, locale, context, org, pool_id, override_id, form)


@router.post(
    "/organisations/{org}/capacity/{pool_id}/exceptions/{override_id}",
    response_class=HTMLResponse,
)
async def update_exception(
    request: Request,
    locale: Locale,
    org: str,
    pool_id: int,
    override_id: int,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Change an exception; existing appointments stay (decision P3)."""
    form = await read_form(request, locale)
    override = read_override(form)
    if override is not None:
        try:
            changed = await pool_planning_service(request).update_override(
                context.principal, org, pool_id, override_id, override, form.text("expected")
            )
        except DomainError as error:
            if _changed(error):
                return await _exception_page(
                    request, locale, context, org, pool_id, override_id, form, conflict=error
                )
            _refused(form, error, locale)
        else:
            return redirect_done(
                _exceptions_path(org, pool_id), "saved" if changed else "unchanged"
            )
    return await _exception_page(request, locale, context, org, pool_id, override_id, form, 422)


@router.post(
    "/organisations/{org}/capacity/{pool_id}/exceptions/{override_id}/delete",
    response_class=HTMLResponse,
)
async def remove_exception(
    request: Request,
    locale: Locale,
    org: str,
    pool_id: int,
    override_id: int,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Remove an exception, if it is still the one the page showed."""
    form = await read_form(request, locale)
    try:
        await pool_planning_service(request).remove_override(
            context.principal, org, pool_id, override_id, form.text("expected")
        )
    except DomainError as error:
        if not _changed(error):
            raise_unknown(error)
        return await _exceptions_response(
            request, locale, context, org, pool_id, _notice(locale, error), status=409
        )
    return redirect_done(_exceptions_path(org, pool_id), "removed")
