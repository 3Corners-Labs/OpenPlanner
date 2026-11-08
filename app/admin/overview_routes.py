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
    GET  /admin/overview/w/status          settings.platform.view  version, updates, storage
    GET  /admin/overview/w/alerts          dashboard.view          open alerts in reach (OV-6)

A widget that the register marks live refreshes itself (ADR 0061 §4, OV-5): its body is a
``data-live-fragment`` that the shared ``static/live-region.js`` (ADR 0072 §6) asks for every
``overview_poll_seconds`` with the key of what it shows; the fragment route answers ``204``
while the key is current. ``static/alert-popups.js`` pops up the alerts that a refresh of
*Alerts* brought, at the levels the admin chose (§7). Without script, or with
``overview_live_enabled`` off, the page shows what it loaded with and a *Refresh* link.

No PII in any fragment: counts, group names, times and admins' display names only (ADR 0061
§8). The queue widget compares the longest wait with the organisation's ``QUEUE_WAIT_*``
settings (OV-7) and says so in words, not only in colour.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, Response

from app.adapters.db.maintenance import measure_storage, storage_limits
from app.adapters.heartbeat import read_heartbeat
from app.admin.address_view import address_problem
from app.admin.alerts import AlertView, alert_views, read_alerts
from app.admin.audit_routes import audit_views
from app.admin.authz import (
    AdminContext,
    RequirePermission,
    admin_service,
    can_function,
    maintenance_service,
)
from app.admin.closure_routes import closure_service
from app.admin.live import key
from app.admin.overview_charts import (
    ChartView,
    ViewChoice,
    chart_view,
    explicit_views,
    view_choices,
)
from app.admin.pages import Locale, admin_moment, page, role_labels, scope_text, templates
from app.admin.scope_choice import filter_view, scope_of
from app.admin.session import session_alert_prefs
from app.admin.shortfalls import agenda_service, shortfall_view
from app.admin.update_routes import update_service
from app.config import Settings
from app.domain.admin import LOGIN_EVENT, LOGOUT_EVENT
from app.domain.alerts import AlertLevel, at_least, lowest_popup
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
from app.domain.storage import MIB, StorageGrade, assess
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
from app.web.widgets import OVERVIEW_PATH, View, Widget, resolve_view, visible, widget

router = APIRouter(prefix="/admin", include_in_schema=False)

ViewName = Annotated[str | None, Query(max_length=16)]
"""The ``view`` a chart-capable fragment is asked for; anything unsupported is the default."""

Seen = Annotated[str | None, Query(max_length=64)]
"""The key of the fragment the page shows; a live widget answers 204 while it is current."""

LEVEL_PARAM = "alerts-level"
"""The address parameter of the *Alerts* level filter, on the page and on its fragment."""

LEVEL_FILTERS: dict[str, AlertLevel] = {
    "": AlertLevel.INFO,
    "warning": AlertLevel.WARNING,
    "outage": AlertLevel.OUTAGE,
}
"""The level filter's choices: all, warnings and outages, outages only (ADR 0061 §7)."""

LEVEL_FILTER_LABELS: dict[str, str] = {
    "": "admin.overview.widget.alerts.filter.all",
    "warning": "admin.overview.widget.alerts.filter.warning",
    "outage": "admin.overview.widget.alerts.filter.outage",
}
"""The words of each choice of the level filter."""

STORAGE_GRADES: dict[StorageGrade, tuple[str, str]] = {
    StorageGrade.OK: ("admin.overview.widget.status.storage.ok", "success"),
    StorageGrade.LOW: ("admin.overview.widget.status.storage.low", "warning"),
    StorageGrade.CRITICAL: ("admin.overview.widget.status.storage.critical", "danger"),
}
"""The words and chip tone of each storage grade on *System status*."""

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
        poll: The address ``live-region.js`` asks, with the key of what is shown; ``None`` for
            a widget that does not refresh itself.
        poll_seconds: How often it asks (``overview_poll_seconds``).
        key: The key of what is shown; the fragment route compares it with ``seen``.
    """

    widget: Widget
    rows: list[object]
    total: int
    more_href: str | None = None
    extra: dict[str, object] | None = None
    shown: View = View.LIST
    chart: ChartView | None = None
    choices: tuple[ViewChoice, ...] = ()
    poll: str | None = None
    poll_seconds: int = 0
    key: str = ""


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
    """One own queue as the widget lists it (counts and a duration, ADR 0061 §8).

    ``wait_state`` says in words how the longest wait compares with the organisation's target
    and warning threshold (OV-7); ``wait_tone`` repeats it in colour.
    """

    name: str
    organisation: str
    href: str
    waiting: int
    longest_wait_minutes: int
    staff_available: int
    wait_state: str = ""
    wait_tone: str = ""


@dataclass(frozen=True)
class LevelChoice:
    """One link of the *Alerts* level filter."""

    label: str
    href: str
    current: bool


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


def _organisation_settings(request: Request, organisation: str) -> Settings:
    """Return one organisation's settings (its class D values over the platform's)."""
    live = getattr(request.app.state, "live_settings", None)
    if live is None:
        return _settings(request)
    found: Settings = live.for_organisation(live.organisation_id(organisation))
    return found


def _wait_state(_: BoundGettext, minutes: int, settings: Settings) -> tuple[str, str]:
    """Return how ``minutes`` of longest wait compares with the target and the warning (OV-7)."""
    if minutes > settings.queue_wait_warning_minutes:
        return (
            _(
                "admin.overview.widget.queue.over_warning",
                minutes=settings.queue_wait_warning_minutes,
            ),
            "danger",
        )
    if minutes > settings.queue_wait_target_minutes:
        return (
            _(
                "admin.overview.widget.queue.over_target",
                minutes=settings.queue_wait_target_minutes,
            ),
            "warning",
        )
    return (
        _("admin.overview.widget.queue.on_target", minutes=settings.queue_wait_target_minutes),
        "success",
    )


async def _queue(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Queue across own groups: per own queue in reach, waiting, longest wait, staff signed in."""
    item = _declared("queue")
    service: QueueService = request.app.state.queue_service
    lifetime = timedelta(hours=_settings(request).admin_session_hours)
    choice = (await scope_of(request, principal))[0]
    rows = []
    for s in await service.summaries(principal, lifetime):
        if not choice.keeps(s.summary.organisation_slug, s.summary.group.slug):
            continue
        minutes = int(s.longest_wait.total_seconds() // 60)
        state, tone = _wait_state(
            locale.gettext, minutes, _organisation_settings(request, s.summary.organisation_slug)
        )
        rows.append(
            QueueRow(
                s.summary.group.name,
                s.summary.organisation_name,
                f"/admin/groups/{s.summary.group.slug}/queue",
                s.waiting,
                minutes,
                s.staff_available,
                state,
                tone,
            )
        )
    return WidgetView(item, list(rows[: item.default_rows]), len(rows))


def _level_filter(request: Request) -> str:
    """Return the level filter the address asks for; anything unknown is *all*."""
    asked = request.query_params.get(LEVEL_PARAM, "")
    return asked if asked in LEVEL_FILTERS else ""


async def _alerts(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Alerts: the open alerts in reach, most urgent first, narrowed by the level filter."""
    _ = locale.gettext
    item = _declared("alerts")
    chosen = _level_filter(request)
    found = at_least(await read_alerts(request, principal), LEVEL_FILTERS[chosen])
    rows = alert_views(request, locale, principal, found)
    outages = sum(1 for a in found if a.level is AlertLevel.OUTAGE)
    shown_view = request.query_params.get(item.key)
    levels = [
        LevelChoice(
            _(label),
            _with_query(
                OVERVIEW_PATH,
                {item.key: shown_view if shown_view in {v.value for v in item.views} else ""},
                {LEVEL_PARAM: value},
            )
            + f"#widget-{item.key}",
            value == chosen,
        )
        for value, label in LEVEL_FILTER_LABELS.items()
    ]
    extra: dict[str, object] = {
        "levels": levels,
        "status": _("admin.overview.widget.alerts.status", count=len(rows), outages=outages),
    }
    more = (
        "/admin/audit?source=system&problems=1"
        if can_function(principal)(Permission.AUDIT_VIEW)
        else None
    )
    return WidgetView(item, list(rows), len(rows), more, extra)


def _with_query(path: str, *parts: Mapping[str, str]) -> str:
    """Return ``path`` with the non-empty values of ``parts`` as its query."""
    query = {k: v for part in parts for k, v in part.items() if v}
    return f"{path}?{urlencode(query)}" if query else path


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


async def _plaintext_mail(
    request: Request, principal: Principal, settings: Settings, can: Callable[[Permission], bool]
) -> int:
    """Count the mail servers that run without TLS in production (ADR 0091 point 4).

    Stored SMTP settings of organisations plus the ``SMTP_*`` fallback; zero for anyone
    without ``integrations.manage``, outside production, or with the host's exception set.
    """
    if not can(Permission.INTEGRATIONS_MANAGE):
        return 0
    service = request.app.state.channel_service
    count = len(await service.plaintext_relays(principal))
    if settings.environment == "prod" and not settings.smtp_plaintext_relay_allowed:
        count += int(settings.smtp_fallback_plaintext)
    return count


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
    plaintext_mail = await _plaintext_mail(request, principal, settings, can)
    proxy_problem = await address_problem(request, locale, settings)
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
    storage = _storage_row(_, settings)
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
    if storage is not None:
        # A low or full disk comes before the quiet rows, like the other problems below.
        rows.insert(2 if storage.tone != "success" else len(rows), storage)
    if plaintext_mail:
        # An error is shown before the quiet rows, so the default row count never hides it.
        rows.insert(
            2,
            StatusRow(
                _("admin.overview.widget.status.mail"),
                _("admin.overview.widget.status.mail.plaintext", count=plaintext_mail),
                "danger",
                "/admin/integrations",
            ),
        )
    if proxy_problem:
        # One row for the whole *Address and proxy* part of System status (ADR 0086): forwarded
        # headers ignored, a round trip with a finding, or an unencrypted proxy hop (ADR 0091).
        rows.insert(
            2,
            StatusRow(
                _("admin.overview.widget.status.proxy"),
                proxy_problem[0],
                proxy_problem[1],
                "/admin/settings#address-heading",
            ),
        )
    return WidgetView(item, list(rows[: item.default_rows]), len(rows), "/admin/settings")


def _storage_row(_: BoundGettext, settings: Settings) -> StatusRow | None:
    """Return the storage line: free space and database size, graded (ADR 0040).

    Measured here, on the volume the web process shares with the worker; ``None`` when the
    database is not a file that can be measured.
    """
    usage = measure_storage(settings.database_url)
    if usage is None:
        return None
    grade, _reason = assess(usage, storage_limits(settings))
    text_key, tone = STORAGE_GRADES[grade]
    return StatusRow(
        _("admin.overview.widget.status.storage"),
        _(text_key, free=int(usage.free_percent), size=usage.database_bytes // MIB),
        tone,
    )


RENDERERS: dict[str, Renderer] = {
    "alerts": _alerts,
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
    """The queue widget's chart data: participants waiting per group."""
    return [Datum(r.name, r.waiting) for r in rows if isinstance(r, QueueRow)]


def _alerts_data(rows: list[object]) -> list[Datum]:
    """The alerts widget's chart data: open alerts per level, most urgent first."""
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, AlertView):
            counts[row.level_label] = counts.get(row.level_label, 0) + 1
    return [Datum(label, count) for label, count in counts.items()]


CHART_DATA: dict[str, Callable[[list[object]], list[Datum]]] = {
    "bookings-today": _bookings_data,
    "queue": _queue_data,
    "alerts": _alerts_data,
}
"""The widgets that can draw a chart, and the data of their one series; see ``CHART_SERIES``."""


def _shaped(
    request: Request,
    view: WidgetView,
    locale: RequestLocale,
    shown: View,
    selected: Mapping[str, View],
    on_page: Mapping[str, Widget],
) -> WidgetView:
    """Give ``view`` the view to draw, its chart, its switcher links and its poll address.

    ``selected`` are the views the address asks for, so a link switching one widget keeps the
    others as they are; ``on_page`` are the widgets the address may name. The poll address
    carries the key of all of that, so the fragment route can answer 204 while it holds.
    """
    _ = locale.gettext
    item = view.widget
    data = CHART_DATA[item.key](view.rows) if item.key in CHART_DATA else []
    chart = chart_view(shown, item, data, _(item.title_key), _)
    choices = view_choices(item, shown, selected, on_page, _)
    level = _level_filter(request) if item.key == "alerts" else ""
    if level:
        choices = tuple(
            replace(c, href=_with_query(c.href, {LEVEL_PARAM: level}))
            if "?" not in c.href
            else replace(c, href=f"{c.href}&{urlencode({LEVEL_PARAM: level})}")
            for c in choices
        )
    shaped = replace(view, shown=shown, chart=chart, choices=choices)
    settings = _settings(request)
    if not (item.live and settings.overview_live_enabled):
        return shaped
    current = key(shaped.rows, shaped.total, shaped.more_href, shaped.extra, shown.value, chart)
    poll = _with_query(
        item.path,
        {"view": shown.value if shown is not item.views[0] else ""},
        {LEVEL_PARAM: level},
        {"seen": current},
    )
    return replace(shaped, poll=poll, poll_seconds=settings.overview_poll_seconds, key=current)


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
            request,
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
    seen: str | None = None,
) -> Response:
    """Answer one widget without the shell, in the view ``requested`` if it supports it.

    A live widget answers ``204`` while ``seen`` is the key of what it would render, so a quiet
    overview costs one empty response per widget and interval.
    """
    item = view.widget
    shown = resolve_view(item, requested)
    shaped = _shaped(request, view, locale, shown, {item.key: shown}, {item.key: item})
    if seen and seen == shaped.key:
        return Response(status_code=204, headers={"cache-control": "no-store"})
    return render(templates, request, FRAME, locale, {"view": shaped, "principal": principal})


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
    closures: Annotated[str, Query(max_length=8)] = "",
) -> HTMLResponse:
    """The overview: the visible widgets as a grid, then the groups in scope with shortfalls.

    ``?closures=1`` narrows the groups to those with a closure in force now (ADR 0047 §4), each
    linking to its *Closure* tab; any other value (at most 8 characters) shows every group.
    """
    principal = context.principal
    views = await _views(request, locale, principal)
    choice, in_scope = await scope_of(request, principal)
    groups = [g for g in in_scope if choice.keeps(g.organisation_slug, g.group.slug)]
    closed = await closure_service(request).active([g.group for g in groups])
    only_closed = closures == "1"
    total = len(groups)
    if only_closed:
        groups = [g for g in groups if g.group.slug in closed]
    summaries = await agenda_service(request).summaries(principal)
    shortfalls = {
        g.group.slug: shortfall_view(summaries[g.group.slug], g.group, locale)
        for g in groups
        if g.group.slug in summaries and summaries[g.group.slug].count
    }
    live = _settings(request).overview_live_enabled
    floor = lowest_popup(session_alert_prefs(request).popups)
    return page(
        request,
        locale,
        "admin/dashboard.html",
        principal,
        {
            "live": live,
            # The least urgent level that pops up for this admin; ``None``: no pop-ups (§7).
            "popup_level": floor.value if floor else None,
            "views": views,
            "groups": groups,
            "closed": closed,
            "only_closed": only_closed,
            "total_groups": total,
            "shortfalls": shortfalls,
            "scope_filter": filter_view(
                choice,
                in_scope,
                locale.gettext,
                can_function(principal)(Permission.ORGANISATION_VIEW),
            ),
        },
        scripts=live,
    )


@router.get("/overview/w/rights", response_class=FragmentResponse)
async def rights_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))],
) -> Response:
    """The fragment *My role and reach*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _rights(request, locale, principal))


@router.get("/overview/w/bookings-today", response_class=FragmentResponse)
async def bookings_today_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.BOOKINGS_VIEW))],
    view: ViewName = None,
    seen: Seen = None,
) -> Response:
    """The fragment *Bookings today per group*, as list, bar or pie."""
    principal = context.principal
    shaped = await _bookings_today(request, locale, principal)
    return _fragment(request, locale, principal, shaped, view, seen)


@router.get("/overview/w/changes", response_class=FragmentResponse)
async def changes_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
) -> Response:
    """The fragment *Recent configuration changes*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _changes(request, locale, principal))


@router.get("/overview/w/queue", response_class=FragmentResponse)
async def queue_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.QUEUE_WORK))],
    view: ViewName = None,
    seen: Seen = None,
) -> Response:
    """The fragment *Queue across own groups*, as table, bar or line."""
    principal = context.principal
    shaped = await _queue(request, locale, principal)
    return _fragment(request, locale, principal, shaped, view, seen)


@router.get("/overview/w/tasks", response_class=FragmentResponse)
async def tasks_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
    seen: Seen = None,
) -> Response:
    """The fragment *Running tasks*."""
    principal = context.principal
    shaped = await _tasks(request, locale, principal)
    return _fragment(request, locale, principal, shaped, seen=seen)


@router.get("/overview/w/status", response_class=FragmentResponse)
async def status_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_VIEW))],
    seen: Seen = None,
) -> Response:
    """The fragment *System status*."""
    principal = context.principal
    shaped = await _status(request, locale, principal)
    return _fragment(request, locale, principal, shaped, seen=seen)


@router.get("/overview/w/alerts", response_class=FragmentResponse)
async def alerts_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
    view: ViewName = None,
    seen: Seen = None,
    level: Annotated[str | None, Query(alias=LEVEL_PARAM, max_length=16)] = None,
) -> Response:
    """The fragment *Alerts*, as list, bar or pie, narrowed by the level filter.

    ``level`` is read from the address by the renderer (``LEVEL_PARAM``); it is declared here
    so the bound on its length applies.
    """
    principal = context.principal
    shaped = await _alerts(request, locale, principal)
    return _fragment(request, locale, principal, shaped, view, seen)
