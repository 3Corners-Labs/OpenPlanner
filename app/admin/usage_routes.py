"""The usage reports: a list of the groups in scope and the *Volume* tables (ADR 0117 step 5).

Counts only, no personal data, and small cells are hidden (``< n``) before the page is built:
the page never sees a figure below the threshold. Every route needs ``reports.view``; the
service reads inside the scope of that permission and audits a denial.

    GET /admin/reports                          reports.view   the groups in scope, a count each
    GET /admin/organisations/{org}/usage        reports.view   the organisation's groups added up
    GET /admin/groups/{slug}/usage              reports.view   one group

The two usage pages take ``from`` and ``to`` (whole group-local days) as a GET form, so every
state has a URL and the pages work without JavaScript.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission
from app.admin.group_routes import group_page
from app.admin.pages import Locale, admin_moment, organisation_header, page
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage_report_service import UsageReportService, UsageVolume
from app.web.usage_labels import label

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[AdminContext, Depends(RequirePermission(Permission.REPORTS_VIEW))]
"""``reports.view`` held anywhere; the service narrows it to the page's organisation."""

ViewGroup = Annotated[
    AdminContext, Depends(RequirePermission(Permission.REPORTS_VIEW, group_param="slug"))
]
"""``reports.view`` on the group of the path."""


def report_service(request: Request) -> UsageReportService:
    """Return the application's usage report service."""
    service: UsageReportService = request.app.state.usage_report_service
    return service


def _day(text: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` query value; empty means the default.

    Raises:
        DomainError: ``invalid_range`` for anything else.
    """
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DomainError(ErrorCode.INVALID_RANGE) from None


def _view(request: Request, locale: Locale, result: UsageVolume) -> dict[str, object]:
    """Return the template's context for a *Volume* report: tables with their words."""
    _ = locale.gettext
    report = result.report
    return {
        "usage": result,
        "report": report,
        "as_of": admin_moment(request, locale, report.as_of) if report.as_of else "",
        "label": lambda table, code: label(_, table, code),
        "form_from": report.first_day.isoformat(),
        "form_to": report.last_day.isoformat(),
    }


@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """List the groups in scope with the bookings created in the last four weeks."""
    rows = await report_service(request).headlines(context.principal)
    return page(request, locale, "admin/usage_reports.html", context.principal, {"rows": rows})


@router.get("/organisations/{org}/usage", response_class=HTMLResponse)
async def organisation_usage(
    request: Request,
    locale: Locale,
    org: str,
    context: View,
) -> HTMLResponse:
    """Show the *Volume* report of an organisation's groups in the reader's scope."""
    result = await report_service(request).volume(
        context.principal,
        organisation_slug=org,
        first_day=_day(request.query_params.get("from")),
        last_day=_day(request.query_params.get("to")),
    )
    header = organisation_header(request, locale, context.principal, result.organisation_name, org)
    return page(
        request,
        locale,
        "admin/usage.html",
        context.principal,
        {
            "org_header": header,
            "base": f"/admin/organisations/{org}/usage",
            **_view(request, locale, result),
        },
    )


@router.get("/groups/{slug}/usage", response_class=HTMLResponse)
async def group_usage(
    request: Request, locale: Locale, slug: str, context: ViewGroup
) -> HTMLResponse:
    """Show the *Volume* report of one group."""
    result = await report_service(request).volume(
        context.principal,
        group_slug=slug,
        first_day=_day(request.query_params.get("from")),
        last_day=_day(request.query_params.get("to")),
    )
    return group_page(
        request,
        locale,
        context,
        "admin/usage.html",
        {"base": f"/admin/groups/{slug}/usage", **_view(request, locale, result)},
    )
