"""The audit log page: admin actions and system events, exact per level (ADR 0014, 0031).

Every admin with ``audit.view`` gets the same page; what it lists is their scope and nothing
above it: a group admin sees the events of their own groups, an organisation admin also the
organisation's own events, the superuser also global ones. The filters (source, problems
only, group, booking) are a GET form without JavaScript and only narrow that scope; unknown
or empty values are ignored, never an error. Reasons and error codes are shown next to the
outcome, so a failing reminder says why. A booking id links to the booking's timeline when
the admin may view it.

    GET /admin/audit?source=system&problems=1&group=demo&booking=42     audit.view
    GET /admin/audit?level=warning&category=security&from=2026-10-01&to=2026-10-28&trace=…
    GET /admin/audit/export.csv?<the same parameters>                    audit.export
    POST /admin/audit/diagnostics        target=group:<slug>|org:<slug>   diagnostics.manage
    POST /admin/audit/diagnostics/off    target=…                         diagnostics.manage

ADR 0040, part 3: filters for minimum level, category, period (days in ``ADMIN_TIMEZONE``) and
trace id; a trace id shows the whole chain of one request or job. The export writes exactly
the current filter inside the admin's scope as CSV (``app.domain.audit_export``) and is itself
an audit event; an export over a limit is refused on the page, never cut short.

ADR 0040, part 4: the superuser switches diagnostic mode on for one group or organisation
(``app.domain.diagnostics``); the page lists the modes that are on, each with when it ends.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.forms import Option, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.domain import staff
from app.domain.admin import ANONYMOUS, LOGIN_EVENT, LOGOUT_EVENT
from app.domain.api_access import CALL_EVENT, TOKEN_EVENT
from app.domain.audit import (
    SYSTEM_ACTOR,
    AuditEntry,
    AuditFilter,
    Category,
    Level,
    Outcome,
    Source,
)
from app.domain.audit_export import ExportLimits, ExportRefused
from app.domain.authorization import (
    API_SCOPES,
    ApiScope,
    Permission,
    Principal,
    Reason,
    Role,
    Target,
    decide,
)
from app.domain.diagnostics import BRAKE_EVENT, DiagnosticsService
from app.domain.errors import DomainError
from app.domain.event_context import TRACE_ID
from app.domain.event_schema import rfc3339
from app.domain.maintenance import MaintenanceReason
from app.domain.ports import Clock
from app.domain.roles import ROLE_CREATED, ROLE_DELETED, ROLE_UPDATED
from app.domain.scheduling import GroupSummary
from app.domain.security_log import INVALID_SIGNATURE, TOKEN_REFRESH_FAILED, WEBHOOK_REJECTED
from app.domain.storage import (
    DATABASE_TOO_LARGE,
    FREE_SPACE_CRITICAL,
    FREE_SPACE_LOW,
    STORAGE_EVENT,
)
from app.i18n import BoundGettext
from app.web.authz_labels import (
    API_SCOPE_LABELS,
    OUTCOME_LABELS,
    PERMISSION_LABELS,
    ROLE_LABELS,
)
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

DATE_LENGTH = len("2026-10-28")

EVENT_LABELS: dict[str, str] = {
    LOGIN_EVENT: "audit.event.login",
    LOGOUT_EVENT: "audit.event.logout",
    TOKEN_EVENT: "audit.event.api_token",
    CALL_EVENT: "audit.event.api_call",
    # ADR 0039: changes to custom roles (global, so only the superuser sees them here; the
    # matrix page shows the same history to every admin).
    ROLE_CREATED: "audit.event.role_created",
    ROLE_UPDATED: "audit.event.role_updated",
    ROLE_DELETED: "audit.event.role_deleted",
    # System events (ADR 0031): one per job type.
    "job.create_video_room": "audit.job.create_video_room",
    "job.delete_video_room": "audit.job.delete_video_room",
    "job.send_confirmation": "audit.job.send_confirmation",
    "job.send_change": "audit.job.send_change",
    "job.send_reminder": "audit.job.send_reminder",
    "job.send_cancellation": "audit.job.send_cancellation",
    "job.dispatch_wxcc_task": "audit.job.dispatch_wxcc_task",
    "job.end_wxcc_task": "audit.job.end_wxcc_task",
    "job.purge_pii": "audit.job.purge_pii",
    "job.send_suspension": "audit.job.send_suspension",
    "job.send_closure_update": "audit.job.send_closure_update",
    "job.send_reinstatement": "audit.job.send_reinstatement",
    # Storage watch (ADR 0040): global, so only the superuser sees it.
    STORAGE_EVENT: "audit.event.storage_check",
    # Staff directory (ADR 0046): first login, and roles or memberships changed at login.
    staff.CREATED_EVENT: "audit.event.staff_created",
    staff.CHANGED_EVENT: "audit.event.staff_changed",
    # Security events (ADR 0034): global, deduplicated per window.
    WEBHOOK_REJECTED: "audit.event.webhook_rejected",
    TOKEN_REFRESH_FAILED: "audit.event.token_refresh",
    # Diagnostic mode (ADR 0040, part 4).
    "job.run": "audit.event.job_run",
    BRAKE_EVENT: "audit.event.diagnostics_brake",
}
"""Readable label per event key that is not a permission."""

DETAIL_LABELS: dict[str, str] = {
    "rule.add": "admin.audit.detail.rule_add",
    "rule.edit": "admin.audit.detail.rule_edit",
    "rule.remove": "admin.audit.detail.rule_remove",
    "rule.split": "admin.audit.detail.rule_split",
    "override.add": "admin.audit.detail.override_add",
    "override.remove": "admin.audit.detail.override_remove",
    "name": "admin.field.name",
    "timezone": "admin.field.timezone",
    "slot_minutes": "admin.field.slot_minutes",
    "min_notice_minutes": "admin.field.min_notice_minutes",
    "horizon_days": "admin.field.horizon_days",
    "phone_mode": "admin.field.phone_mode",
    "dispatch_lead_minutes": "admin.field.dispatch_lead_minutes",
    "video_host_email": "admin.field.video_host_email",
    "wxcc_business_address": "admin.field.wxcc_business_address",
    "wxcc_channel": "admin.field.wxcc_channel",
    "wxcc_routing_value": "admin.field.wxcc_routing_value",
    "direct_call_enabled": "admin.field.direct_call_enabled",
    "embedding_enabled": "admin.field.embedding_enabled",
    "not_picked_up_minutes": "admin.field.not_picked_up_minutes",
    "direct_call_max_concurrent": "admin.field.direct_call_max_concurrent",
    "display_name": "admin.branding.display_name",
    "accent_color": "admin.branding.accent_color",
    "logo": "admin.branding.logo",
    "logo.remove": "admin.audit.detail.logo_remove",
    "theme": "admin.branding.colours_shape",
    "header_style": "admin.branding.header_style",
    "theme_file": "admin.branding.theme_file",
    "font.text": "admin.branding.font.text",
    "font.text-bold": "admin.branding.font.text_bold",
    "font.heading": "admin.branding.font.heading",
    "font.text.remove": "admin.audit.detail.font_remove.text",
    "font.text-bold.remove": "admin.audit.detail.font_remove.text_bold",
    "font.heading.remove": "admin.audit.detail.font_remove.heading",
    "closure.start": "admin.audit.detail.closure_start",
    "closure.update": "admin.audit.detail.closure_update",
    "closure.lift": "admin.audit.detail.closure_lift",
    "closure.image.update": "admin.audit.detail.closure_image_update",
    "closure.image.remove": "admin.audit.detail.closure_image_remove",
    "booking.reinstate": "admin.audit.detail.booking_reinstate",
    "booking.reinstate_all": "admin.audit.detail.booking_reinstate_all",
    "booking.cancel": "admin.audit.detail.booking_cancel",
    "booking.cancel_all": "admin.audit.detail.booking_cancel_all",
    "booking.move": "admin.audit.detail.booking_move",
    "client.create": "admin.audit.detail.client_create",
    "client.rotate": "admin.audit.detail.client_rotate",
    "client.enable": "admin.audit.detail.client_enable",
    "client.disable": "admin.audit.detail.client_disable",
    "client.revoke_tokens": "admin.audit.detail.client_revoke_tokens",
    "groups": "admin.api.groups",
    "scopes": "admin.api.scopes",
    "networks": "admin.api.networks",
    "api.enabled": "admin.api.enabled",
    "api.docs_public": "admin.api.docs_public",
    "channel:email": "admin.audit.detail.channel_email",
    "channel:sms": "admin.audit.detail.channel_sms",
    # ADR 0038: switching maintenance mode, and changes refused while it is on.
    "maintenance.on": "admin.audit.detail.maintenance_on",
    "maintenance.off": "admin.audit.detail.maintenance_off",
    "maintenance.refused": "admin.audit.detail.maintenance_refused",
    "source:webex": "admin.audit.detail.source_webex",
    "source:wxcc": "admin.audit.detail.source_wxcc",
    "diagnostics.on": "admin.audit.detail.diagnostics_on",
    "diagnostics.off": "admin.audit.detail.diagnostics_off",
}
"""Readable label per audit detail key (changed field names and schedule actions)."""

COUNT_LABELS: dict[str, str] = {
    "bookings": "admin.audit.detail.bookings",
    "notified": "admin.audit.detail.notified",
    "file": "admin.audit.detail.file",
    "attempt": "admin.audit.detail.attempt",
    "free_percent": "admin.audit.detail.free_percent",
    "database_mb": "admin.audit.detail.database_mb",
    "suppressed": "admin.audit.detail.suppressed",
    "modes": "admin.audit.detail.modes",
}
"""Readable label per counted audit detail, e.g. ``bookings:3`` (ADR 0024)."""

VALUE_LABELS: dict[str, str] = {
    staff.ROLE_ADDED: "admin.audit.detail.role_add",
    staff.ROLE_REMOVED: "admin.audit.detail.role_remove",
    staff.ORGANISATION_ADDED: "admin.audit.detail.organisation_add",
    staff.ORGANISATION_REMOVED: "admin.audit.detail.organisation_remove",
    staff.GROUP_ADDED: "admin.audit.detail.group_add",
    staff.GROUP_REMOVED: "admin.audit.detail.group_remove",
}
"""Readable label per audit detail with a code or slug, e.g. ``role.add:org_admin`` (ADR 0046)."""

REASON_LABELS: dict[str, str] = {
    Reason.NO_ROLE: "admin.audit.reason.no_role",
    Reason.NOT_PERMITTED: "admin.audit.reason.not_permitted",
    Reason.OUT_OF_SCOPE: "admin.audit.reason.out_of_scope",
    Reason.MAINTENANCE: "admin.audit.reason.maintenance",
    # ADR 0038: why maintenance was switched on (the reason codes of the switch).
    **{reason.value: f"admin.maintenance.reason.{reason.value}" for reason in MaintenanceReason},
    "stale": "admin.audit.reason.stale",
    "no_recipient": "admin.audit.reason.no_recipient",
    "no_booking": "admin.audit.reason.no_booking",
    "channel_not_configured": "admin.audit.reason.channel_not_configured",
    FREE_SPACE_LOW: "admin.audit.reason.free_space_low",
    FREE_SPACE_CRITICAL: "admin.audit.reason.free_space_critical",
    DATABASE_TOO_LARGE: "admin.audit.reason.database_too_large",
    INVALID_SIGNATURE: "admin.audit.reason.invalid_signature",
    "unauthorized": "admin.audit.reason.unauthorized",
    "unreachable": "admin.audit.reason.unreachable",
}
"""Readable explanation per known reason; any other code (an error code) is shown as is."""

OUTCOME_TONES: dict[Outcome, str] = {
    Outcome.SUCCESS: "success",
    Outcome.DENIED: "danger",
    Outcome.FAILURE: "danger",
    Outcome.RETRY: "warning",
    Outcome.SKIPPED: "",
}
"""Chip colour per audit outcome; the outcome is always also written out (WCAG 1.4.1)."""

LEVEL_LABELS: dict[Level, str] = {
    Level.DEBUG: "admin.audit.level.debug",
    Level.INFO: "admin.audit.level.info",
    Level.WARNING: "admin.audit.level.warning",
    Level.ERROR: "admin.audit.level.error",
    Level.CRITICAL: "admin.audit.level.critical",
}
"""Label per minimum level in the filter (ADR 0040)."""

CATEGORY_LABELS: dict[Category, str] = {
    Category.AUDIT: "admin.audit.category.audit",
    Category.SYSTEM: "admin.audit.category.system",
    Category.SECURITY: "admin.audit.category.security",
    Category.DIAGNOSTIC: "admin.audit.category.diagnostic",
}
"""Label per category in the filter (ADR 0040)."""

EXPORT_REFUSALS: dict[str, str] = {
    "too_many_days": "admin.audit.export.too_many_days",
    "too_many_rows": "admin.audit.export.too_many_rows",
}
"""Why an export was refused, per refusal code."""


@dataclass(frozen=True)
class AuditView:
    """A translated audit event."""

    when: str
    utc: str
    actor: str
    action: str
    outcome: str
    target: str
    tone: str
    detail: str
    reason: str
    code: str
    booking_id: int | None
    booking_url: str | None
    trace_url: str | None


@dataclass(frozen=True)
class DiagnosticsView:
    """Diagnostic mode on the audit page, for the superuser.

    Attributes:
        active: The modes that are on: target label, value for the off button, end.
        targets: Every organisation and group it can be switched on for.
    """

    active: tuple[tuple[str, str, str], ...]
    targets: tuple[Option, ...]


@dataclass(frozen=True)
class FilterView:
    """The filter form: the choices and what is selected now."""

    levels: tuple[Option, ...]
    level: str
    categories: tuple[Option, ...]
    category: str
    groups: tuple[Option, ...]
    group: str
    problems: bool
    booking: str
    date_from: str
    date_to: str
    trace: str
    active: bool
    query: str
    """The current filter as a query string, for the export link."""


def _actor_label(entry: AuditEntry, _: BoundGettext) -> str:
    """Name the actor: display name, "not signed in", "system", or the subject."""
    if entry.event.actor == ANONYMOUS:
        return _("audit.actor.anonymous")
    if entry.event.source is Source.SYSTEM and entry.event.actor == SYSTEM_ACTOR:
        return _("audit.actor.system")
    return entry.actor_name or entry.event.actor


def _detail_label(detail: str, _: BoundGettext) -> str:
    """Translate an audit detail such as ``horizon_days,name`` into readable labels."""
    labels = []
    for key in (key for key in detail.split(",") if key):
        name, _sep, count = key.partition(":")
        if name in COUNT_LABELS and count.isdigit():
            labels.append(_(COUNT_LABELS[name], count=int(count)))
        elif name in VALUE_LABELS:
            value = _(ROLE_LABELS[Role(count)]) if count in set(Role) else count
            labels.append(_(VALUE_LABELS[name], value=value))
        else:
            labels.append(_(DETAIL_LABELS[key]) if key in DETAIL_LABELS else key)
    return ", ".join(labels)


def _action_label(action: str, _: BoundGettext) -> str:
    """Translate a permission; event keys such as ``admin.login`` have their own labels."""
    if action in EVENT_LABELS:
        return _(EVENT_LABELS[action])
    if action in API_SCOPES:
        return _(API_SCOPE_LABELS[ApiScope(action)])
    try:
        return _(PERMISSION_LABELS[Permission(action)])
    except ValueError:
        return action


def _reason_label(reason: str, _: BoundGettext) -> str:
    """Explain a known reason; an unknown one is an error code, shown as the code only."""
    return _(REASON_LABELS[reason]) if reason in REASON_LABELS else ""


def _booking_url(principal: Principal, entry: AuditEntry) -> str | None:
    """Link to the booking's timeline when the admin may view the group's bookings."""
    event = entry.event
    if event.booking_id is None or event.group is None or event.organisation is None:
        return None
    target = Target.for_group(event.group, event.organisation)
    if not decide(principal, Permission.BOOKINGS_VIEW, target).allowed:
        return None
    return f"/admin/groups/{event.group}/bookings/{event.booking_id}"


def _date(value: str | None) -> date | None:
    """Parse ``YYYY-MM-DD``; anything else is ignored."""
    try:
        return date.fromisoformat(value) if value and len(value) == DATE_LENGTH else None
    except ValueError:
        return None


def _midnight(day: date | None, zone: ZoneInfo) -> datetime | None:
    """Return the start of ``day`` in ``zone`` as UTC."""
    if day is None:
        return None
    return datetime.combine(day, time(), zone).astimezone(UTC)


@dataclass(frozen=True)
class AuditQuery:
    """The query parameters of the audit page and its export, as typed by the browser."""

    source: str | None = None
    problems: str | None = None
    group: str | None = None
    booking: str | None = None
    level: str | None = None
    category: str | None = None
    date_from: str | None = Query(default=None, alias="from")
    date_to: str | None = Query(default=None, alias="to")
    trace: str | None = None

    def narrow(self, groups: set[str], zone: ZoneInfo) -> AuditFilter:
        """Turn the parameters into a filter; unknown or empty values are ignored.

        Dates are days in the admin time zone; ``to`` includes the whole day.
        """
        until = _date(self.date_to)
        return AuditFilter(
            source=Source(self.source) if self.source in set(Source) else None,
            problems_only=self.problems == "1",
            group=self.group if self.group in groups else None,
            booking_id=(
                int(self.booking)
                if self.booking and self.booking.isdigit() and len(self.booking) <= 12
                else None
            ),
            min_level=Level(self.level) if self.level in set(Level) else None,
            category=Category(self.category) if self.category in set(Category) else None,
            since=_midnight(_date(self.date_from), zone),
            until=_midnight(until + timedelta(days=1), zone) if until else None,
            trace_id=self.trace if self.trace and TRACE_ID.fullmatch(self.trace) else None,
        )


def _filter_view(
    narrow: AuditFilter, in_scope: list[GroupSummary], zone: ZoneInfo, _: BoundGettext
) -> FilterView:
    """Describe the filter form with what ``narrow`` selected."""
    date_to = narrow.until.astimezone(zone).date() - timedelta(days=1) if narrow.until else None
    values = {
        "level": narrow.min_level.value if narrow.min_level else "",
        "category": narrow.category.value if narrow.category else "",
        "group": narrow.group or "",
        "problems": "1" if narrow.problems_only else "",
        "booking": str(narrow.booking_id) if narrow.booking_id else "",
        "from": narrow.since.astimezone(zone).date().isoformat() if narrow.since else "",
        "to": date_to.isoformat() if date_to else "",
        "trace": narrow.trace_id or "",
        "source": narrow.source.value if narrow.source else "",
    }
    return FilterView(
        levels=tuple(Option(level.value, _(LEVEL_LABELS[level])) for level in Level),
        level=values["level"],
        categories=tuple(Option(c.value, _(CATEGORY_LABELS[c])) for c in Category),
        category=values["category"],
        groups=tuple(Option(g.group.slug, f"{g.group.name} ({g.group.slug})") for g in in_scope),
        group=values["group"],
        problems=narrow.problems_only,
        booking=values["booking"],
        date_from=values["from"],
        date_to=values["to"],
        trace=values["trace"],
        active=narrow != AuditFilter(),
        query=urlencode({key: value for key, value in values.items() if value}),
    )


def audit_views(
    request: Request, locale: RequestLocale, principal: Principal, entries: list[AuditEntry]
) -> list[AuditView]:
    """Translate audit entries for ``_audit_table.html`` (the log and a staff member's page)."""
    _ = locale.gettext
    return [
        AuditView(
            admin_moment(request, locale, e.at),
            rfc3339(e.at),
            _actor_label(entry, _),
            _action_label(e.action, _),
            _(OUTCOME_LABELS[e.outcome]),
            " / ".join(t for t in (e.organisation, e.group) if t) or "–",
            OUTCOME_TONES[e.outcome],
            _detail_label(e.detail, _),
            _reason_label(e.reason, _),
            e.reason,
            e.booking_id,
            _booking_url(principal, entry),
            f"/admin/audit?trace={e.trace_id}" if e.trace_id else None,
        )
        for entry in entries
        for e in (entry.event,)
    ]


async def _audit_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    query: AuditQuery,
    error: str | None = None,
) -> HTMLResponse:
    """Render the audit page for ``query``, with an export refusal when there is one."""
    service = admin_service(request)
    zone = ZoneInfo(request.app.state.settings.admin_timezone)
    in_scope = await service.groups(context.principal, Permission.AUDIT_VIEW)
    narrow = query.narrow({g.group.slug for g in in_scope}, zone)
    events = await service.audit_log(context.principal, narrow)
    response = _page(
        request,
        locale,
        "admin/audit.html",
        context.principal,
        {
            "events": audit_views(request, locale, context.principal, events),
            "filters": _filter_view(narrow, in_scope, zone, locale.gettext),
            "export_error": locale.gettext(EXPORT_REFUSALS[error]) if error else None,
            "diagnostics": await _diagnostics_view(request, locale, context),
            "done": done_message(request, locale.gettext),
        },
    )
    if error:
        response.status_code = 422
    return response


async def _diagnostics_view(
    request: Request, locale: RequestLocale, context: AdminContext
) -> DiagnosticsView | None:
    """Describe diagnostic mode for the superuser; ``None`` for everyone else."""
    if not can_function(context.principal)(Permission.DIAGNOSTICS_MANAGE):
        return None
    service: DiagnosticsService = request.app.state.diagnostics_service
    groups = await admin_service(request).groups(context.principal, Permission.GROUP_VIEW)
    organisations = sorted({(g.organisation_slug, g.organisation_name) for g in groups})
    targets = [Option(f"org:{slug}", f"{name} ({slug})") for slug, name in organisations] + [
        Option(f"group:{g.group.slug}", f"{g.organisation_name} / {g.group.name}") for g in groups
    ]
    labels = {option.value: option.label for option in targets}
    active = []
    for mode in await service.active():
        value = f"group:{mode.group}" if mode.group else f"org:{mode.organisation}"
        active.append((labels.get(value, value), value, admin_moment(request, locale, mode.until)))
    return DiagnosticsView(tuple(active), tuple(targets))


def _target(value: str, groups: list[GroupSummary]) -> tuple[str, str | None] | None:
    """Turn ``group:<slug>`` or ``org:<slug>`` into (organisation, group); else ``None``."""
    kind, _sep, slug = value.partition(":")
    for summary in groups:
        if kind == "group" and summary.group.slug == slug:
            return summary.organisation_slug, slug
        if kind == "org" and summary.organisation_slug == slug:
            return slug, None
    return None


async def _switch(
    request: Request, locale: RequestLocale, context: AdminContext, on: bool
) -> Response:
    """Switch diagnostic mode for the posted target; an unknown target changes nothing."""
    form = await read_form(request, locale)
    groups = await admin_service(request).groups(context.principal, Permission.GROUP_VIEW)
    target = _target(form.text("target"), groups)
    if target is None:
        return redirect_done("/admin/audit", "unchanged", anchor="diagnostics")
    service: DiagnosticsService = request.app.state.diagnostics_service
    try:
        if on:
            await service.switch_on(context.principal, *target)
        else:
            await service.switch_off(context.principal, *target)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done("/admin/audit", "unchanged", anchor="diagnostics")
    done = "diagnostics_on" if on else "diagnostics_off"
    return redirect_done("/admin/audit", done, anchor="diagnostics")


ManageDiagnostics = Annotated[
    AdminContext, Depends(RequirePermission(Permission.DIAGNOSTICS_MANAGE, fresh=True))
]


@router.post("/audit/diagnostics", response_model=None)
async def diagnostics_on(
    request: Request, locale: Locale, _: SameOrigin, context: ManageDiagnostics
) -> Response:
    """Switch diagnostic mode on for one group or organisation (ADR 0040, part 4)."""
    return await _switch(request, locale, context, on=True)


@router.post("/audit/diagnostics/off", response_model=None)
async def diagnostics_off(
    request: Request, locale: Locale, _: SameOrigin, context: ManageDiagnostics
) -> Response:
    """Switch diagnostic mode off for one group or organisation."""
    return await _switch(request, locale, context, on=False)


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
    query: Annotated[AuditQuery, Depends()],
) -> HTMLResponse:
    """Show the newest audit events and system events in scope, optionally filtered."""
    return await _audit_page(request, locale, context, query)


@router.get("/audit/export.csv", response_model=None)
async def export(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_EXPORT))],
    query: Annotated[AuditQuery, Depends()],
) -> Response:
    """Download exactly the current filter as CSV (ADR 0040, part 3); the export is audited."""
    service = admin_service(request)
    settings = request.app.state.settings
    zone = ZoneInfo(settings.admin_timezone)
    in_scope = await service.groups(context.principal, Permission.AUDIT_EXPORT)
    narrow = query.narrow({g.group.slug for g in in_scope}, zone)
    limits = ExportLimits(settings.audit_export_max_rows, settings.audit_export_max_days)
    try:
        document, _rows = await service.export_audit(
            context.principal, narrow, settings.event_envelope, limits
        )
    except ExportRefused as refused:
        return await _audit_page(request, locale, context, query, refused.code)
    clock: Clock = request.app.state.clock
    name = f"audit-{clock.now():%Y%m%dT%H%M%SZ}.csv"
    return Response(
        document.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "cache-control": "no-store",
        },
    )
