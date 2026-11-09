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
has a URL and the pages work without JavaScript. One permission covers every view, except
``team`` on the organisation's page: the load per team member needs ``reports.staff.view`` and
the organisation's own switch (``reporting_staff_view_enabled``, ADR 0117 point 9), and is never
exported.

The two export routes take the same ``from`` and ``to`` and a ``view``: a report family (the
screen's tables as CSV) or ``tidy`` (the hourly file ``openplanner.usage/1``). The file is sent
with ``X-Content-SHA256`` and ``Cache-Control: no-store``. Every export is audited, so only the
portal itself may ask for one (``SameOrigin``, ASVS V3.5.3). An export over a limit is audited
too and sends the admin back to the report page with the reason (``refused``), so nothing at
the export's own address can run it again.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, TypedDict
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.forms import (
    done_message,
    raise_refusal,
    redirect_done,
    redirect_refused,
    refused_message,
)
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, admin_moment, organisation_header, page
from app.admin.wallboard_routes import WALLBOARD, wallboard_page, wallboard_shown
from app.domain.authorization import Permission, Principal
from app.domain.csv_export import ExportRefusal, ExportRefused
from app.domain.errors import DomainError, ErrorCode
from app.domain.report_digest import MAX_MONTH_DAY, DigestPeriod
from app.domain.report_digest_service import DigestAdmin
from app.domain.usage_export_service import TIDY, UsageExportService, UsageFile
from app.domain.usage_report import SPLITS, RangedReport
from app.domain.usage_report_service import UsageReportService, UsageScreen
from app.web.usage_charts import capacity_charts, channels_charts, queue_charts, volume_charts
from app.web.usage_labels import duration_text, label
from app.web.usage_tiles import capacity_tiles, volume_tiles

router = APIRouter(prefix="/admin", include_in_schema=False)

VIEWS: tuple[str, ...] = ("volume", "queue", "capacity", "channels")
"""The closed set of report families a usage page shows (ADR 0117 point 11); *Team* follows."""
TEAM = "team"
"""The report family of the load per team member: the organisation's page only (point 9)."""
VIEW_LABELS: dict[str, str] = {
    TEAM: "reports.view.team",
    "volume": "reports.view.volume",
    "queue": "reports.view.queue",
    "capacity": "reports.view.capacity",
    "channels": "reports.view.channels",
    WALLBOARD: "reports.view.wallboard",
}
TEMPLATES: dict[str, str] = {
    TEAM: "admin/usage_team.html",
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
    if view not in VIEWS and view != TEAM:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return view


def _split(request: Request) -> str | None:
    """Return the cut asked for (``split``): none, or one of ``SPLITS``.

    Raises:
        DomainError: ``invalid_range`` for any other value; it is never echoed.
    """
    split = request.query_params.get("split") or None
    if split is not None and split not in SPLITS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return split


def _nav(
    base: str,
    first: str | None,
    last: str | None,
    *,
    team_on: bool = False,
    wallboard_on: bool = False,
) -> list[tuple[str, str, str]]:
    """Return the links between the report families: code, label key and address.

    The days are carried over to the families that have days; the wallboard is about today.
    """
    codes = (*VIEWS, *((TEAM,) if team_on else ()), *((WALLBOARD,) if wallboard_on else ()))
    days = {"from": first, "to": last} if first and last else {}
    return [
        (
            code,
            VIEW_LABELS[code],
            f"{base}?{urlencode({'view': code, **({} if code == WALLBOARD else days)})}",
        )
        for code in codes
    ]


def _view(
    request: Request,
    locale: Locale,
    result: UsageScreen[RangedReport],
    view: str,
    base: str,
    team_on: bool = False,
    wallboard_on: bool = False,
) -> dict[str, object]:
    """Return the template's context shared by every report: range, words and the view links.

    ``team_on`` adds the *Team* link, which only the organisation's page of a reader with
    ``reports.staff.view`` has, while the organisation has the team view on. ``screen_export``
    says whether the report on screen can be exported as it is: every family but the team,
    which is never exported. ``export_error`` is why an export of the report was refused,
    after it sent the admin back here.
    """
    _ = locale.gettext
    report = result.report
    first, last = report.first_day.isoformat(), report.last_day.isoformat()
    return {
        "usage": result,
        "report": report,
        "view": view,
        "views": _nav(base, first, last, team_on=team_on, wallboard_on=wallboard_on),
        "as_of": admin_moment(request, locale, report.as_of) if report.as_of else "",
        "label": lambda table, code: label(_, table, code),
        "duration": lambda seconds, signed=False: duration_text(_, seconds, signed=signed),
        "form_from": first,
        "form_to": last,
        "base": base,
        "screen_export": view in VIEWS,
        "export_error": refused_message(request, EXPORT_REFUSALS, _),
    }


async def _report(
    request: Request,
    locale: Locale,
    principal: Principal,
    base: str,
    *,
    organisation: str | None = None,
    group: str | None = None,
    team_on: bool = False,
    wallboard_on: bool = False,
) -> tuple[str, UsageScreen[Any], dict[str, object]]:
    """Read the report family the query asks for (``view``) and return its page parts.

    Raises:
        DomainError: ``invalid_range`` for ``team`` on a group's page, which has none.
    """
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

    def drill(day: date) -> str:
        """The page for one day: the drill-down link of a chart's day (suppression applies)."""
        query = {"view": view, "from": day.isoformat(), "to": day.isoformat()}
        return f"{base}?{urlencode(query)}"

    if view == TEAM:
        if organisation is None:
            raise DomainError(ErrorCode.INVALID_RANGE)
        result = await service.team(
            principal,
            organisation_slug=organisation,
            first_day=scope["first_day"],
            last_day=scope["last_day"],
        )
    elif view == "queue":
        result = await service.queue(principal, **scope)
        extra["charts"] = queue_charts(result.report, _, drill)
    elif view == "capacity":
        result = await service.capacity(principal, **scope)
        extra["charts"] = capacity_charts(result.report, _, drill)
        extra["tiles_"] = capacity_tiles(result.report, _)
    elif view == "channels":
        result = await service.channels(principal, **scope)
        extra["charts"] = channels_charts(result.report, _)
    else:
        split = _split(request)
        result = await service.volume(principal, **scope, split=split)
        extra["charts"] = volume_charts(result.report, _, drill)
        extra["tiles_"] = volume_tiles(result.report, _)
        extra["split"] = split or ""
    context = {**_view(request, locale, result, view, base, team_on, wallboard_on), **extra}
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
) -> HTMLResponse:
    """Render an organisation's usage report; ``extra`` adds to the template's context."""
    base = f"/admin/organisations/{org}/usage"
    shown = can_function(principal)(Permission.REPORTS_STAFF_VIEW, organisation=org)
    team_on = shown and await report_service(request).team_view_on(principal, org)
    wallboard_on = wallboard_shown(principal, organisation=org)
    if request.query_params.get("view") == WALLBOARD:  # the service refuses (audited) without it
        nav = _nav(base, None, None, team_on=team_on, wallboard_on=True)
        return await wallboard_page(request, locale, principal, base, nav, organisation=org)
    template, result, ctx = await _report(
        request,
        locale,
        principal,
        base,
        organisation=org,
        team_on=team_on,
        wallboard_on=wallboard_on,
    )
    header = organisation_header(request, locale, principal, result.organisation_name, org)
    digest = await digest_context(request, locale, principal, org)
    return page(
        request,
        locale,
        template,
        principal,
        {
            "org_header": header,
            "team_shown": shown,
            "team_on": team_on,
            **ctx,
            **digest,
            **(extra or {}),
        },
        scripts=True,
    )


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
    base = f"/admin/groups/{slug}/usage"
    wallboard_on = wallboard_shown(context.principal, group=context.group)
    if request.query_params.get("view") == WALLBOARD:  # the service refuses (audited) without it
        nav = _nav(base, None, None, wallboard_on=True)
        return await wallboard_page(request, locale, context.principal, base, nav, group=context)
    template, _result, ctx = await _report(
        request, locale, context.principal, base, group=slug, wallboard_on=wallboard_on
    )
    return group_page(request, locale, context, template, ctx, scripts=True)


@dataclass(frozen=True)
class _Asked:
    """The file an export asks for, parsed once from the query.

    Attributes:
        view: A report family (the screen's tables) or ``tidy`` (the hourly file).
        first_day: The first day asked for; ``None`` for the default range.
        last_day: The last day asked for; ``None`` for the default range.
    """

    view: str
    first_day: date | None
    last_day: date | None


def _asked(request: Request) -> _Asked:
    """Return the file the query asks for: ``view`` (``volume`` by default), ``from`` and ``to``.

    Raises:
        DomainError: ``invalid_range`` for a ``view`` that is neither a report family nor
            ``tidy``, or a day that is not ``YYYY-MM-DD``; the value is never echoed.
    """
    view = request.query_params.get("view") or "volume"
    if view != TIDY and view not in VIEWS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return _Asked(
        view, _day(request.query_params.get("from")), _day(request.query_params.get("to"))
    )


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
    ExportRefusal.TOO_MANY_DAYS: "reports.export.refused.too_many_days",
    ExportRefusal.TOO_MANY_ROWS: "reports.export.refused.too_many_rows",
}
"""Why an export was refused, per refusal code (the keys of the page's message)."""


async def _export(
    request: Request,
    principal: Principal,
    asked: _Asked,
    base: str,
    *,
    organisation: str | None = None,
    group: str | None = None,
) -> Response:
    """Send the file ``asked`` for inside the principal's ``reports.export`` scope.

    An export over a limit sends the admin back to the report page ``base`` with the reason:
    the family asked for (``tidy`` shows ``volume``) over the same days. Nothing at the
    export's own address shows the refusal, so a reload, a language switch or a login there
    cannot run the export again.
    """
    try:
        sent = await export_service(request).export(
            principal,
            asked.view,
            organisation_slug=organisation,
            group_slug=group,
            first_day=asked.first_day,
            last_day=asked.last_day,
        )
    except ExportRefused as refused:
        back = {"view": asked.view if asked.view in VIEWS else "volume"}
        if asked.first_day is not None:
            back["from"] = asked.first_day.isoformat()
        if asked.last_day is not None:
            back["to"] = asked.last_day.isoformat()
        return redirect_refused(f"{base}?{urlencode(back)}", refused.code)
    return _file(sent)


@router.get("/organisations/{org}/usage/export.csv", response_model=None)
async def organisation_export(
    request: Request, org: str, _: SameOrigin, context: Export
) -> Response:
    """Download a CSV of an organisation's usage (the screen's tables, or the tidy file).

    The export is audited, so a request another site makes is refused (``SameOrigin``).
    """
    asked = _asked(request)
    base = f"/admin/organisations/{org}/usage"
    return await _export(request, context.principal, asked, base, organisation=org)


@router.get("/groups/{slug}/usage/export.csv", response_model=None)
async def group_export(
    request: Request, slug: str, _: SameOrigin, context: ExportGroup
) -> Response:
    """Download a CSV of one group's usage (the screen's tables, or the tidy file).

    The export is audited, so a request another site makes is refused (``SameOrigin``).
    """
    asked = _asked(request)
    return await _export(
        request, context.principal, asked, f"/admin/groups/{slug}/usage", group=slug
    )


DIGEST_ERRORS: dict[str, str] = {
    "recipients": "reports.digest.error.recipients",
    "limit": "reports.digest.error.limit",
    "schedule": "reports.digest.error.schedule",
}
"""Why a subscription was refused, by the field of the ``DomainError`` (never its value)."""


def digest_admin(request: Request) -> DigestAdmin:
    """Return the application's digest service."""
    service: DigestAdmin = request.app.state.digest_admin
    return service


ManageDigest = Annotated[AdminContext, Depends(RequirePermission(Permission.REPORTS_DIGEST_MANAGE))]
"""``reports.digest.manage`` held anywhere; the service narrows it to the organisation."""


def _recipient_ids(values: dict[str, list[str]]) -> list[int]:
    """Return the staff row ids ticked in the form; anything that is no number is dropped."""
    return [int(v) for v in values.get("recipient", []) if v.isdigit() and len(v) <= 9]


async def digest_context(
    request: Request, locale: Locale, principal: Principal, org: str
) -> dict[str, object]:
    """Return what the digest section of the organisation's Usage page shows.

    Empty for who does not hold ``reports.digest.manage`` on the organisation, so the page for a
    reader is the page it was.
    """
    if not can_function(principal)(Permission.REPORTS_DIGEST_MANAGE, organisation=org):
        return {}
    _ = locale.gettext
    periods = {
        "daily": _("reports.digest.period.daily"),
        "weekly": _("reports.digest.period.weekly"),
        "monthly": _("reports.digest.period.monthly"),
    }
    weekdays = [
        _("reports.digest.weekday.0"),
        _("reports.digest.weekday.1"),
        _("reports.digest.weekday.2"),
        _("reports.digest.weekday.3"),
        _("reports.digest.weekday.4"),
        _("reports.digest.weekday.5"),
        _("reports.digest.weekday.6"),
    ]
    admin = digest_admin(request)
    rows = await admin.rows(principal, org)
    groups = await _groups_of(request, principal, org)
    return {
        "digest": {
            "rows": rows,
            "candidates": await admin.candidates(principal, org, None),
            "groups": groups,
            "periods": periods,
            "weekdays": weekdays,
            "period_options": list(periods.items()),
            "weekday_options": list(enumerate(weekdays)),
            "statuses": {
                "sent": _("reports.digest.status.sent"),
                "skipped_late": _("reports.digest.status.skipped_late"),
                "no_recipient": _("reports.digest.status.no_recipient"),
            },
            "month_days": list(range(1, MAX_MONTH_DAY + 1)),
            "done": done_message(request, locale.gettext),
        }
    }


async def _groups_of(request: Request, principal: Principal, org: str) -> list[tuple[str, str]]:
    """Return the organisation's groups (slug, name) the reader may attach a digest to."""
    found = await admin_service(request).groups(principal, Permission.REPORTS_VIEW)
    return [(g.group.slug, g.group.name) for g in found if g.organisation_slug == org]


@router.post("/organisations/{org}/usage/digest", response_model=None)
async def digest_create(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: ManageDigest
) -> Response:
    """Subscribe staff to the digest of the organisation or of one group (ADR 0117 point 16)."""
    raw = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
    first = {k: v[0] for k, v in raw.items() if v}
    period = first.get("period", "")
    refused: str | None = None
    try:
        if period not in {p.value for p in DigestPeriod}:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "schedule")
        weekday = int(first["weekday"]) if first.get("weekday", "").isdigit() else 0
        month_day = int(first["month_day"]) if first.get("month_day", "").isdigit() else 1
        await digest_admin(request).create(
            context.principal,
            org,
            group_slug=first.get("group") or None,
            period=DigestPeriod(period),
            weekday=weekday,
            month_day=month_day,
            recipients=_recipient_ids(raw),
        )
    except DomainError as error:
        raise_refusal(error)
        refused = error.field or "recipients"
    if refused is not None:
        response = await _organisation_page(
            request,
            locale,
            context.principal,
            org,
            {
                "digest_error": locale.gettext(
                    DIGEST_ERRORS.get(refused, DIGEST_ERRORS["recipients"])
                )
            },
        )
        response.status_code = 422
        return response
    return redirect_done(f"/admin/organisations/{org}/usage", "digest_added", anchor="digest")


@router.post("/organisations/{org}/usage/digest/{subscription_id}/delete", response_model=None)
async def digest_delete(
    request: Request,
    locale: Locale,
    org: str,
    subscription_id: int,
    _: SameOrigin,
    context: ManageDigest,
) -> Response:
    """Delete a digest subscription of the organisation."""
    try:
        await digest_admin(request).delete(context.principal, org, subscription_id)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(f"/admin/organisations/{org}/usage", "unchanged", anchor="digest")
    return redirect_done(f"/admin/organisations/{org}/usage", "digest_removed", anchor="digest")
