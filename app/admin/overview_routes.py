"""The overview as a grid of widgets, without script (ADR 0061 steps 1 and 2, OV-1 to OV-3).

Every widget is a server-rendered fragment with its own route and its own permission, both
from the widget register ``app.web.widgets``. The page renders the visible widgets of the
admin's role default once at load, with a *Refresh* link; the fragment routes answer the same
HTML without the shell, for the live script of a later step (OV-5). A widget the admin may not
see is not rendered and its route refuses (the matrix, ADR 0014).

    GET  /admin/                           dashboard.view          the grid, then the group cards
    GET  /admin/overview/w/rights          rights.view             my role and reach
    GET  /admin/overview/w/bookings-today  bookings.view           bookings today, per group
    GET  /admin/overview/w/changes         audit.view              recent configuration changes
    GET  /admin/overview/w/queue           queue.work              own queues: waiting, wait, staff
    GET  /admin/overview/w/tasks           dashboard.view          running and failed jobs
    GET  /admin/overview/w/status          settings.platform.view  version, updates, maintenance

No PII in any fragment: counts, group names, times and admins' display names only (ADR 0061
§8). The queue widget shows plain values; the on/over-target colouring waits for the
``QUEUE_WAIT_*`` settings of OV-7. *Alerts* is declared in the register but not available: it
needs the alert model of OV-6, so it has no route here.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse

from app.adapters.heartbeat import read_heartbeat
from app.admin.audit_routes import audit_views
from app.admin.authz import (
    AdminContext,
    RequirePermission,
    admin_service,
    can_function,
    maintenance_service,
)
from app.admin.overview_charts import (
    ChartView,
    ViewChoice,
    chart_view,
    explicit_views,
    view_choices,
)
from app.admin.pages import Locale, admin_moment, page, role_labels, scope_text, templates
from app.admin.scope_choice import filter_view, scope_of
from app.admin.shortfalls import agenda_service, shortfall_view
from app.admin.update_routes import update_service
from app.config import Settings
from app.domain.admin import LOGIN_EVENT, LOGOUT_EVENT
from app.domain.audit import AuditEntry, AuditFilter, Category, Outcome, Source
from app.domain.authorization import (
    READ_PERMISSIONS,
    Permission,
    Principal,
    permissions_for,
)
from app.domain.diagnostics import DiagnosticsService
from app.domain.ports import Clock
from app.domain.queue_service import QueueService
from app.domain.staff import CREATED_EVENT
from app.domain.tasks import JobState
from app.domain.update_service import UpdateNotice
from app.domain.updates import UpdateStatus
from app.i18n import BoundGettext
from app.web.authz_labels import PERMISSION_LABELS
from app.web.charts import Datum
from app.web.job_labels import JOB_TYPE_LABELS
from app.web.locale import RequestLocale
from app.web.templates import FragmentResponse, render
from app.web.update_labels import STATUS_LABELS
from app.web.widgets import View, Widget, resolve_view, visible, widget

router = APIRouter(prefix="/admin", include_in_schema=False)

ViewName = Annotated[str | None, Query(max_length=16)]
"""The ``view`` a chart-capable fragment is asked for; anything unsupported is the default."""

FRAME = "admin/widgets/_frame.html"
"""The template that renders one widget: title, body, empty state and footer."""

NOT_CHANGES = frozenset(
    {p.value for p in READ_PERMISSIONS}
    | {Permission.AUDIT_EXPORT.value, LOGIN_EVENT, LOGOUT_EVENT, CREATED_EVENT}
)
"""Successful admin actions that changed no configuration: reads, the export (ADR 0040),
logins, logouts and the staff record a first login creates (ADR 0046)."""


@dataclass(frozen=True)
class WidgetView:
    """One rendered widget: its declaration and what its template needs.

    Attributes:
        widget: The register entry.
        rows: The rows to show; the body template knows their shape.
        total: How many rows there are in all; more than ``rows`` shows a "more" link.
        more_href: Where the whole list lives, if the admin may open it.
        extra: Further template context (the rights widget's role chips).
        shown: The view the widget draws (ADR 0061 §5); its first view unless one is asked for.
        chart: The chart of a bar, line or pie view; ``None`` shows the list, table or empty state.
        choices: The links that switch the widget between its views; none for a single view.
    """

    widget: Widget
    rows: list[object]
    total: int
    more_href: str | None = None
    extra: dict[str, object] | None = None
    shown: View = View.LIST
    chart: ChartView | None = None
    choices: tuple[ViewChoice, ...] = ()


@dataclass(frozen=True)
class GroupCount:
    """Today's confirmed bookings of one group (counts only, ADR 0061 §8)."""

    name: str
    organisation: str
    count: int
    href: str


@dataclass(frozen=True)
class Change:
    """One configuration change as the widget lists it."""

    when: str
    actor: str
    action: str
    target: str
    href: str


@dataclass(frozen=True)
class QueueRow:
    """One own queue as the widget lists it (counts and a duration, ADR 0061 §8)."""

    name: str
    organisation: str
    href: str
    waiting: int
    longest_wait_minutes: int
    staff_available: int


@dataclass(frozen=True)
class TaskRow:
    """One running or failed job, written out: type, state, moment, attempts."""

    label: str
    state: str
    tone: str
    since: str
    attempts: str


@dataclass(frozen=True)
class StatusRow:
    """One line of *System status*: a label, its value in words, a tone that repeats it."""

    label: str
    value: str
    tone: str = ""
    href: str | None = None


JOB_STATE_LABELS: dict[JobState, tuple[str, str]] = {
    JobState.RUNNING: ("admin.overview.widget.tasks.state.running", "accent"),
    JobState.FAILED: ("admin.overview.widget.tasks.state.failed", "danger"),
}
"""Label and tone per job state."""

Renderer = Callable[[Request, Locale, Principal], Awaitable[WidgetView]]


async def _rights(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """My role and reach: the roles and the first permissions with their scope."""
    _ = locale.gettext
    rights = [
        (_(PERMISSION_LABELS[permission]), scope_text(scope, _))
        for permission, scope in permissions_for(principal)
    ]
    item = _declared("rights")
    return WidgetView(
        item,
        list(rights[: item.default_rows]),
        len(rights),
        "/admin/rights",
        {"roles": role_labels(principal, locale)},
    )


async def _bookings_today(
    request: Request, locale: RequestLocale, principal: Principal
) -> WidgetView:
    """Bookings today per group: a count per group whose bookings the admin may view."""
    service = admin_service(request)
    choice = (await scope_of(request, principal))[0]
    groups = [
        g
        for g in await service.groups(principal, Permission.BOOKINGS_VIEW)
        if choice.keeps(g.organisation_slug, g.group.slug)
    ]
    counts = await agenda_service(request).counts_today(principal)
    rows = [
        GroupCount(
            g.group.name,
            g.organisation_name,
            counts.get(g.group.slug, 0),
            f"/admin/groups/{g.group.slug}/agenda",
        )
        for g in sorted(groups, key=lambda g: (-counts.get(g.group.slug, 0), g.group.name))
    ]
    item = _declared("bookings-today")
    return WidgetView(item, list(rows[: item.default_rows]), len(rows))


def _is_change(entry: AuditEntry) -> bool:
    """Whether an audit entry records a successful change of the configuration."""
    event = entry.event
    return event.outcome is Outcome.SUCCESS and event.action not in NOT_CHANGES


async def _changes(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Recent configuration changes: successful admin actions in the admin's audit scope."""
    item = _declared("changes")
    narrow = AuditFilter(source=Source.ADMIN, category=Category.AUDIT)
    entries = await admin_service(request).audit_log(principal, narrow, limit=item.default_rows * 4)
    choice = (await scope_of(request, principal))[0]
    changes = [
        e for e in entries if _is_change(e) and choice.keeps(e.event.organisation, e.event.group)
    ]
    views = audit_views(request, locale, principal, changes[: item.default_rows])
    rows = [
        Change(v.when, v.actor, v.action, v.target, v.trace_url or "/admin/audit") for v in views
    ]
    return WidgetView(item, list(rows), len(changes), "/admin/audit")


def _settings(request: Request) -> Settings:
    """Return the settings the process started with."""
    settings: Settings = request.app.state.settings
    return settings


async def _queue(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Queue across own groups: per own queue in reach, waiting, longest wait, staff signed in."""
    item = _declared("queue")
    service: QueueService = request.app.state.queue_service
    lifetime = timedelta(hours=_settings(request).admin_session_hours)
    choice = (await scope_of(request, principal))[0]
    rows = [
        QueueRow(
            s.summary.group.name,
            s.summary.organisation_name,
            f"/admin/groups/{s.summary.group.slug}/queue",
            s.waiting,
            int(s.longest_wait.total_seconds() // 60),
            s.staff_available,
        )
        for s in await service.summaries(principal, lifetime)
        if choice.keeps(s.summary.organisation_slug, s.summary.group.slug)
    ]
    return WidgetView(item, list(rows[: item.default_rows]), len(rows))


async def _tasks(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Running tasks: the running and failed background jobs, by type, state and moment."""
    _ = locale.gettext
    item = _declared("tasks")
    active = await admin_service(request).active_jobs(item.default_rows)
    rows = []
    for job in active.jobs:
        state_key, tone = JOB_STATE_LABELS[job.state]
        rows.append(
            TaskRow(
                _(JOB_TYPE_LABELS[job.type]),
                _(state_key),
                tone,
                _(
                    "admin.overview.widget.tasks.since",
                    time=admin_moment(request, locale, job.since),
                ),
                _("admin.overview.widget.tasks.attempts", count=job.attempts),
            )
        )
    return WidgetView(item, list(rows), active.total)


def _update_row(_: BoundGettext, notice: UpdateNotice) -> tuple[str, str]:
    """Return the words and tone of the update line: running, problem, available or current."""
    outcome = notice.outcome
    if notice.open is not None:
        version = str(notice.open.target)
        return _("admin.overview.widget.status.update.running", version=version), "accent"
    if outcome is not None and outcome.status is not UpdateStatus.SUCCEEDED:
        text = _(
            "admin.overview.widget.status.update.problem",
            version=str(outcome.target),
            status=_(STATUS_LABELS[outcome.status]),
        )
        return text, "danger"
    if notice.available is not None:
        version = str(notice.available.version)
        return _("admin.overview.widget.status.update.available", version=version), "warning"
    return _("admin.overview.widget.status.update.current"), "success"


async def _status(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """System status: what the updates page, maintenance mode and the health checks know."""
    _ = locale.gettext
    item = _declared("status")
    can = can_function(principal)
    settings = _settings(request)
    clock: Clock = request.app.state.clock
    now = clock.now()
    updates = update_service(request)
    summary = await updates.summary()
    maintenance = await maintenance_service(request).status()
    diagnostics: DiagnosticsService = request.app.state.diagnostics_service
    modes = len(await diagnostics.active())
    problems = 0 if settings.environment == "prod" else len(settings.production_problems())
    updates_href = "/admin/updates" if can(Permission.SYSTEM_UPDATE) else None
    version = (
        str(updates.current) if updates.current else _("admin.overview.widget.status.version.none")
    )
    update_text, update_tone = _update_row(_, summary)
    if maintenance.environment:
        maintenance_row = StatusRow(
            _("admin.overview.widget.status.maintenance"),
            _("admin.overview.widget.status.maintenance.environment"),
            "warning",
        )
    elif maintenance.active and maintenance.state.since:
        since = admin_moment(request, locale, maintenance.state.since)
        maintenance_row = StatusRow(
            _("admin.overview.widget.status.maintenance"),
            _("admin.overview.widget.status.maintenance.on", time=since),
            "warning",
        )
    else:
        maintenance_row = StatusRow(
            _("admin.overview.widget.status.maintenance"),
            _("admin.overview.widget.status.maintenance.off"),
            "success",
        )
    if can(Permission.SYSTEM_MAINTENANCE):
        maintenance_row = StatusRow(
            maintenance_row.label, maintenance_row.value, maintenance_row.tone, "/admin/maintenance"
        )
    beat = read_heartbeat(settings.worker_heartbeat_path)
    if beat is None:
        worker = StatusRow(
            _("admin.overview.widget.status.worker"),
            _("admin.overview.widget.status.worker.unknown"),
            "warning",
        )
    elif now - beat <= timedelta(seconds=settings.worker_heartbeat_max_age_seconds):
        worker = StatusRow(
            _("admin.overview.widget.status.worker"),
            _(
                "admin.overview.widget.status.worker.alive",
                time=admin_moment(request, locale, beat),
            ),
            "success",
        )
    else:
        worker = StatusRow(
            _("admin.overview.widget.status.worker"),
            _(
                "admin.overview.widget.status.worker.stale",
                time=admin_moment(request, locale, beat),
            ),
            "danger",
        )
    rows = [
        StatusRow(_("admin.overview.widget.status.version"), version, "", updates_href),
        StatusRow(_("admin.overview.widget.status.update"), update_text, update_tone, updates_href),
        maintenance_row,
        worker,
        StatusRow(
            _("admin.overview.widget.status.diagnostics"),
            _("admin.overview.widget.status.diagnostics.on", count=modes)
            if modes
            else _("admin.overview.widget.status.diagnostics.off"),
            "warning" if modes else "",
        ),
        StatusRow(
            _("admin.overview.widget.status.environment"),
            _(
                "admin.overview.widget.status.environment.problems",
                environment=settings.environment,
                count=problems,
            )
            if problems
            else _("admin.overview.widget.status.environment.ok", environment=settings.environment),
            "warning" if problems else "",
            "/admin/settings",
        ),
    ]
    return WidgetView(item, list(rows[: item.default_rows]), len(rows), "/admin/settings")


RENDERERS: dict[str, Renderer] = {
    "rights": _rights,
    "bookings-today": _bookings_today,
    "changes": _changes,
    "queue": _queue,
    "tasks": _tasks,
    "status": _status,
}
"""The widgets this step renders; a test checks them against the register."""


def _declared(key: str) -> Widget:
    """Return the register entry of ``key``; a renderer without one is a programming error."""
    found = widget(key)
    if found is None or not found.available:  # pragma: no cover - programming error
        raise LookupError(key)
    return found


def _bookings_data(rows: list[object]) -> list[Datum]:
    """The bookings widget's chart data: bookings today per group."""
    return [Datum(r.name, r.count) for r in rows if isinstance(r, GroupCount)]


def _queue_data(rows: list[object]) -> list[Datum]:
    """The queue widget's chart data: citizens waiting per group."""
    return [Datum(r.name, r.waiting) for r in rows if isinstance(r, QueueRow)]


CHART_DATA: dict[str, Callable[[list[object]], list[Datum]]] = {
    "bookings-today": _bookings_data,
    "queue": _queue_data,
}
"""The widgets that can draw a chart, and the data of their one series; see ``CHART_SERIES``."""


def _shaped(
    view: WidgetView,
    locale: RequestLocale,
    shown: View,
    selected: Mapping[str, View],
    on_page: Mapping[str, Widget],
) -> WidgetView:
    """Give ``view`` the view to draw, its chart if that view is one, and its switcher links.

    ``selected`` are the views the address asks for, so a link switching one widget keeps the
    others as they are; ``on_page`` are the widgets the address may name.
    """
    _ = locale.gettext
    item = view.widget
    data = CHART_DATA[item.key](view.rows) if item.key in CHART_DATA else []
    chart = chart_view(shown, item, data, _(item.title_key), _)
    choices = view_choices(item, shown, selected, on_page, _)
    return replace(view, shown=shown, chart=chart, choices=choices)


async def _views(request: Request, locale: RequestLocale, principal: Principal) -> list[WidgetView]:
    """Render every visible widget of the admin's default layout, in the views asked for.

    The address names a view per widget (``/admin/?bookings-today=bar``); what it asks that a
    widget does not support is ignored (``resolve_view``).
    """
    allowed = can_function(principal)
    items = visible(principal, allowed)
    on_page = {w.key: w for w in items}
    selected = explicit_views(on_page, request.query_params)
    return [
        _shaped(
            await RENDERERS[w.key](request, locale, principal),
            locale,
            selected.get(w.key, w.views[0]),
            selected,
            on_page,
        )
        for w in items
    ]


def _fragment(
    request: Request,
    locale: RequestLocale,
    principal: Principal,
    view: WidgetView,
    requested: str | None = None,
) -> HTMLResponse:
    """Answer one widget without the shell, in the view ``requested`` if it supports it."""
    item = view.widget
    shown = resolve_view(item, requested)
    shaped = _shaped(view, locale, shown, {item.key: shown}, {item.key: item})
    return render(templates, request, FRAME, locale, {"view": shaped, "principal": principal})


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
) -> HTMLResponse:
    """The overview: the visible widgets as a grid, then the groups in scope with shortfalls."""
    principal = context.principal
    views = await _views(request, locale, principal)
    choice, in_scope = await scope_of(request, principal)
    groups = [g for g in in_scope if choice.keeps(g.organisation_slug, g.group.slug)]
    summaries = await agenda_service(request).summaries(principal)
    shortfalls = {
        g.group.slug: shortfall_view(summaries[g.group.slug], g.group, locale)
        for g in groups
        if g.group.slug in summaries and summaries[g.group.slug].count
    }
    return page(
        request,
        locale,
        "admin/dashboard.html",
        principal,
        {
            "views": views,
            "groups": groups,
            "shortfalls": shortfalls,
            "scope_filter": filter_view(
                choice,
                in_scope,
                locale.gettext,
                can_function(principal)(Permission.ORGANISATION_MANAGE),
            ),
        },
    )


@router.get("/overview/w/rights", response_class=FragmentResponse)
async def rights_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))],
) -> HTMLResponse:
    """The fragment *My role and reach*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _rights(request, locale, principal))


@router.get("/overview/w/bookings-today", response_class=FragmentResponse)
async def bookings_today_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.BOOKINGS_VIEW))],
    view: ViewName = None,
) -> HTMLResponse:
    """The fragment *Bookings today per group*, as list, bar or pie."""
    principal = context.principal
    shaped = await _bookings_today(request, locale, principal)
    return _fragment(request, locale, principal, shaped, view)


@router.get("/overview/w/changes", response_class=FragmentResponse)
async def changes_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
) -> HTMLResponse:
    """The fragment *Recent configuration changes*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _changes(request, locale, principal))


@router.get("/overview/w/queue", response_class=FragmentResponse)
async def queue_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.QUEUE_WORK))],
    view: ViewName = None,
) -> HTMLResponse:
    """The fragment *Queue across own groups*, as table, bar or line."""
    principal = context.principal
    return _fragment(request, locale, principal, await _queue(request, locale, principal), view)


@router.get("/overview/w/tasks", response_class=FragmentResponse)
async def tasks_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
) -> HTMLResponse:
    """The fragment *Running tasks*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _tasks(request, locale, principal))


@router.get("/overview/w/status", response_class=FragmentResponse)
async def status_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_VIEW))],
) -> HTMLResponse:
    """The fragment *System status*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _status(request, locale, principal))
