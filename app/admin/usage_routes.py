"""The usage reports: a list of the groups in scope and the report families (ADR 0117 steps 5, 6).

Counts only, no personal data, and small cells are hidden (``< n``) before the page is built:
the page never sees a figure below the threshold. Every route needs ``reports.view``; the
service reads inside the scope of that permission and audits a denial.

    GET /admin/reports                          reports.view   the groups in scope, a count each
    GET /admin/organisations/{org}/usage        reports.view   the organisation's groups added up
    GET /admin/groups/{slug}/usage              reports.view   one group

The two usage pages take ``view`` (``volume``, ``queue``, ``capacity`` or ``channels``, ADR 0117
point 11), ``from`` and ``to`` (whole group-local days) as a GET form and links, so every state
has a URL and the pages work without JavaScript. One permission covers every view.
"""

from datetime import date
from typing import Annotated, Any, TypedDict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission
from app.admin.group_routes import group_page
from app.admin.pages import Locale, admin_moment, organisation_header, page
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage_report import RangedReport
from app.domain.usage_report_service import UsageReportService, UsageScreen
from app.web.usage_charts import capacity_charts, channels_charts, queue_charts
from app.web.usage_labels import duration_text, label

router = APIRouter(prefix="/admin", include_in_schema=False)

VIEWS: tuple[str, ...] = ("volume", "queue", "capacity", "channels")
"""The closed set of report families a usage page shows (ADR 0117 point 11); *Team* follows."""
VIEW_LABELS: dict[str, str] = {
    "volume": "reports.view.volume",
    "queue": "reports.view.queue",
    "capacity": "reports.view.capacity",
    "channels": "reports.view.channels",
}
TEMPLATES: dict[str, str] = {
    "volume": "admin/usage.html",
    "queue": "admin/usage_queue.html",
    "capacity": "admin/usage_capacity.html",
    "channels": "admin/usage_channels.html",
}

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


class _Query(TypedDict):
    """The arguments every report read takes: what it is about and the range asked for."""

    organisation_slug: str | None
    group_slug: str | None
    first_day: date | None
    last_day: date | None


def _which(request: Request) -> str:
    """Return the report family asked for (``view``), ``volume`` by default.

    Raises:
        DomainError: ``invalid_range`` (shown as an invalid request) for a family that is not
            one of ``VIEWS``; the value is never echoed.
    """
    view = request.query_params.get("view") or "volume"
    if view not in VIEWS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return view


def _view(
    request: Request,
    locale: Locale,
    result: UsageScreen[RangedReport],
    view: str,
    base: str,
) -> dict[str, object]:
    """Return the template's context shared by every report: range, words and the view links."""
    _ = locale.gettext
    report = result.report
    first, last = report.first_day.isoformat(), report.last_day.isoformat()
    return {
        "usage": result,
        "report": report,
        "view": view,
        "views": [
            (
                code,
                VIEW_LABELS[code],
                f"{base}?{urlencode({'view': code, 'from': first, 'to': last})}",
            )
            for code in VIEWS
        ],
        "as_of": admin_moment(request, locale, report.as_of) if report.as_of else "",
        "label": lambda table, code: label(_, table, code),
        "duration": lambda seconds, signed=False: duration_text(_, seconds, signed=signed),
        "form_from": first,
        "form_to": last,
        "base": base,
    }


async def _report(
    request: Request,
    locale: Locale,
    principal: Principal,
    base: str,
    *,
    organisation: str | None = None,
    group: str | None = None,
) -> tuple[str, UsageScreen[Any], dict[str, object]]:
    """Read the report family of ``view`` and return its template, result and extra context."""
    view = _which(request)
    service = report_service(request)
    scope = _Query(
        organisation_slug=organisation,
        group_slug=group,
        first_day=_day(request.query_params.get("from")),
        last_day=_day(request.query_params.get("to")),
    )
    _ = locale.gettext
    result: UsageScreen[Any]
    extra: dict[str, object] = {}
    if view == "queue":
        result = await service.queue(principal, **scope)
        extra["charts"] = queue_charts(result.report, _)
    elif view == "capacity":
        result = await service.capacity(principal, **scope)
        extra["charts"] = capacity_charts(result.report, _)
    elif view == "channels":
        result = await service.channels(principal, **scope)
        extra["charts"] = channels_charts(result.report, _)
    else:
        result = await service.volume(principal, **scope)
    context = {**_view(request, locale, result, view, base), **extra}
    return TEMPLATES[view], result, context


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
    """Show a usage report (``view``) of an organisation's groups in the reader's scope."""
    base = f"/admin/organisations/{org}/usage"
    template, result, ctx = await _report(
        request, locale, context.principal, base, organisation=org
    )
    header = organisation_header(request, locale, context.principal, result.organisation_name, org)
    return page(request, locale, template, context.principal, {"org_header": header, **ctx})


@router.get("/groups/{slug}/usage", response_class=HTMLResponse)
async def group_usage(
    request: Request, locale: Locale, slug: str, context: ViewGroup
) -> HTMLResponse:
    """Show a usage report (``view``) of one group."""
    template, _result, ctx = await _report(
        request, locale, context.principal, f"/admin/groups/{slug}/usage", group=slug
    )
    return group_page(request, locale, context, template, ctx)
