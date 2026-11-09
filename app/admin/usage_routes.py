"""The usage reports: a list of the groups in scope and the report families (ADR 0117 steps 5, 6).

Counts only, no personal data, and small cells are hidden (``< n``) before the page is built:
the page never sees a figure below the threshold. Every route needs ``reports.view``; the
service reads inside the scope of that permission and audits a denial.

    GET /admin/reports                          reports.view   the groups in scope, a count each
    GET /admin/organisations/{org}/usage        reports.view   the organisation's groups added up
    GET /admin/groups/{slug}/usage              reports.view   one group
    GET /admin/organisations/{org}/usage/export.csv   reports.export  a CSV of the organisation's
    GET /admin/groups/{slug}/usage/export.csv         reports.export  a CSV of one group

The two usage pages take ``view`` (``volume``, ``queue``, ``capacity`` or ``channels``, ADR 0117
point 11), ``from`` and ``to`` (whole group-local days) as a GET form and links, so every state
has a URL and the pages work without JavaScript. One permission covers every view.

The two export routes take the same ``from`` and ``to`` and a ``view``: a report family (the
screen's tables as CSV) or ``tidy`` (the hourly file ``openplanner.usage/1``). The file is sent
with ``X-Content-SHA256`` and ``Cache-Control: no-store``; an export over a limit shows the
report page again with the reason, and is audited either way.
"""

from datetime import date
from typing import Annotated, Any, TypedDict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.group_routes import group_page
from app.admin.pages import Locale, admin_moment, organisation_header, page
from app.domain.authorization import Permission, Principal
from app.domain.csv_export import ExportRefused
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage_export_service import TIDY, UsageExportService, UsageFile
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

Export = Annotated[AdminContext, Depends(RequirePermission(Permission.REPORTS_EXPORT))]
"""``reports.export`` held anywhere; the service narrows it to the page's organisation."""

ExportGroup = Annotated[
    AdminContext, Depends(RequirePermission(Permission.REPORTS_EXPORT, group_param="slug"))
]
"""``reports.export`` on the group of the path."""


def report_service(request: Request) -> UsageReportService:
    """Return the application's usage report service."""
    service: UsageReportService = request.app.state.usage_report_service
    return service


def export_service(request: Request) -> UsageExportService:
    """Return the application's usage export service."""
    service: UsageExportService = request.app.state.usage_export_service
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
    view: str | None = None,
) -> tuple[str, UsageScreen[Any], dict[str, object]]:
    """Read the report family of ``view`` (the query's by default) and return its page parts."""
    view = view or _which(request)
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


async def _organisation_page(
    request: Request,
    locale: Locale,
    principal: Principal,
    org: str,
    extra: dict[str, object] | None = None,
    view: str | None = None,
) -> HTMLResponse:
    """Render an organisation's usage report; ``extra`` adds to the template's context."""
    base = f"/admin/organisations/{org}/usage"
    template, result, ctx = await _report(
        request, locale, principal, base, organisation=org, view=view
    )
    header = organisation_header(request, locale, principal, result.organisation_name, org)
    return page(
        request, locale, template, principal, {"org_header": header, **ctx, **(extra or {})}
    )


async def _group_page(
    request: Request,
    locale: Locale,
    context: AdminContext,
    slug: str,
    extra: dict[str, object] | None = None,
    view: str | None = None,
) -> HTMLResponse:
    """Render one group's usage report; ``extra`` adds to the template's context."""
    template, _result, ctx = await _report(
        request, locale, context.principal, f"/admin/groups/{slug}/usage", group=slug, view=view
    )
    return group_page(request, locale, context, template, {**ctx, **(extra or {})})


@router.get("/organisations/{org}/usage", response_class=HTMLResponse)
async def organisation_usage(
    request: Request,
    locale: Locale,
    org: str,
    context: View,
) -> HTMLResponse:
    """Show a usage report (``view``) of an organisation's groups in the reader's scope."""
    return await _organisation_page(request, locale, context.principal, org)


@router.get("/groups/{slug}/usage", response_class=HTMLResponse)
async def group_usage(
    request: Request, locale: Locale, slug: str, context: ViewGroup
) -> HTMLResponse:
    """Show a usage report (``view``) of one group."""
    return await _group_page(request, locale, context, slug)


def _export_view(request: Request) -> str:
    """Return the file asked for (``view``): a report family, or ``tidy``; ``volume`` by default.

    Raises:
        DomainError: ``invalid_range`` for any other value; it is never echoed.
    """
    view = request.query_params.get("view") or "volume"
    if view != TIDY and view not in VIEWS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return view


def _file(sent: UsageFile) -> Response:
    """Return the export as a download: the checksum and no caching, the file never stored."""
    return Response(
        sent.document.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{sent.name}.csv"',
            "cache-control": "no-store",
            "x-content-sha256": sent.sha256,
        },
    )


EXPORT_REFUSALS: dict[str, str] = {
    "too_many_days": "reports.export.refused.too_many_days",
    "too_many_rows": "reports.export.refused.too_many_rows",
}
"""Why an export was refused, per refusal code (the keys of the page's message)."""


def _refused(locale: Locale, refused: ExportRefused) -> dict[str, object]:
    """Return the context that makes the report page say why the export was refused."""
    return {"export_error": locale.gettext(EXPORT_REFUSALS[refused.code])}


async def _export(
    request: Request, principal: Principal, *, organisation: str | None, group: str | None
) -> UsageFile:
    """Build the file asked for by the query inside the principal's ``reports.export`` scope."""
    return await export_service(request).export(
        principal,
        _export_view(request),
        organisation_slug=organisation,
        group_slug=group,
        first_day=_day(request.query_params.get("from")),
        last_day=_day(request.query_params.get("to")),
    )


@router.get("/organisations/{org}/usage/export.csv", response_model=None)
async def organisation_export(
    request: Request, locale: Locale, org: str, context: Export
) -> Response:
    """Download a CSV of an organisation's usage (the screen's tables, or the tidy file)."""
    try:
        return _file(await _export(request, context.principal, organisation=org, group=None))
    except ExportRefused as refused:
        response = await _organisation_page(
            request, locale, context.principal, org, _refused(locale, refused), _page_view(request)
        )
        response.status_code = 422
        return response


@router.get("/groups/{slug}/usage/export.csv", response_model=None)
async def group_export(
    request: Request, locale: Locale, slug: str, context: ExportGroup
) -> Response:
    """Download a CSV of one group's usage (the screen's tables, or the tidy file)."""
    try:
        return _file(await _export(request, context.principal, organisation=None, group=slug))
    except ExportRefused as refused:
        response = await _group_page(
            request, locale, context, slug, _refused(locale, refused), "volume"
        )
        response.status_code = 422
        return response


def _page_view(request: Request) -> str:
    """Return the report family to show again after a refused export: the asked one, or volume."""
    view = request.query_params.get("view")
    return view if view in VIEWS else "volume"
