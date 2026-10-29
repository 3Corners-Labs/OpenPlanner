"""The audit log page: admin actions and system events, exact per level (ADR 0014, 0031).

Every admin with ``audit.view`` gets the same page; what it lists is their scope and nothing
above it: a group admin sees the events of their own groups, an organisation admin also the
organisation's own events, the superuser also global ones. The filters (source, problems
only, group, booking) are a GET form without JavaScript and only narrow that scope; unknown
or empty values are ignored, never an error. Reasons and error codes are shown next to the
outcome, so a failing reminder says why. A booking id links to the booking's timeline when
the admin may view it.

    GET /admin/audit?source=system&problems=1&group=demo&booking=42     audit.view
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission, admin_service
from app.admin.forms import Option
from app.admin.pages import Locale, admin_moment
from app.admin.pages import page as _page
from app.admin.planner_routes import REASON_LABELS as CHANGE_REASON_LABELS
from app.domain import staff
from app.domain.admin import ANONYMOUS, LOGIN_EVENT, LOGOUT_EVENT
from app.domain.api_access import CALL_EVENT, TOKEN_EVENT
from app.domain.audit import SYSTEM_ACTOR, AuditEntry, AuditFilter, Outcome, Source
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
from app.domain.booking import ChangeReason
from app.domain.event_schema import rfc3339
from app.domain.maintenance import MaintenanceReason
from app.domain.planner_service import ALERT_ACTION
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

EVENT_LABELS: dict[str, str] = {
    LOGIN_EVENT: "audit.event.login",
    LOGOUT_EVENT: "audit.event.logout",
    TOKEN_EVENT: "audit.event.api_token",
    CALL_EVENT: "audit.event.api_call",
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
    # ADR 0044: one planner made many changes within an hour.
    ALERT_ACTION: "audit.event.planner_changes_alert",
    # Storage watch (ADR 0040): global, so only the superuser sees it.
    STORAGE_EVENT: "audit.event.storage_check",
    # Staff directory (ADR 0046): first login, and roles or memberships changed at login.
    staff.CREATED_EVENT: "audit.event.staff_created",
    staff.CHANGED_EVENT: "audit.event.staff_changed",
    # Security events (ADR 0034): global, deduplicated per window.
    WEBHOOK_REJECTED: "audit.event.webhook_rejected",
    TOKEN_REFRESH_FAILED: "audit.event.token_refresh",
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
    "planner_change_min_lead_minutes": "admin.field.planner_change_min_lead_minutes",
    "video_host_email": "admin.field.video_host_email",
    "wxcc_business_address": "admin.field.wxcc_business_address",
    "wxcc_channel": "admin.field.wxcc_channel",
    "wxcc_routing_value": "admin.field.wxcc_routing_value",
    "direct_call_enabled": "admin.field.direct_call_enabled",
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
    "booking.reinstate": "admin.audit.detail.booking_reinstate",
    "booking.reinstate_all": "admin.audit.detail.booking_reinstate_all",
    "booking.cancel": "admin.audit.detail.booking_cancel",
    "booking.cancel_all": "admin.audit.detail.booking_cancel_all",
    "booking.move": "admin.audit.detail.booking_move",
    "planner.move": "admin.audit.detail.planner_move",
    "planner.cancel": "admin.audit.detail.planner_cancel",
    "shortfall.accept": "admin.audit.detail.shortfall_accept",
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
}
"""Readable label per audit detail key (changed field names and schedule actions)."""

COUNT_LABELS: dict[str, str] = {
    "bookings": "admin.audit.detail.bookings",
    "notified": "admin.audit.detail.notified",
    "attempt": "admin.audit.detail.attempt",
    "free_percent": "admin.audit.detail.free_percent",
    "database_mb": "admin.audit.detail.database_mb",
    "changes": "admin.audit.detail.changes",
    "suppressed": "admin.audit.detail.suppressed",
}
"""Readable label per counted audit detail, e.g. ``bookings:3`` (ADR 0024)."""

VALUE_LABELS: dict[str, str] = {
    staff.ROLE_ADDED: "admin.audit.detail.role_add",
    staff.ROLE_REMOVED: "admin.audit.detail.role_remove",
    staff.ORGANISATION_ADDED: "admin.audit.detail.organisation_add",
    staff.ORGANISATION_REMOVED: "admin.audit.detail.organisation_remove",
    staff.GROUP_ADDED: "admin.audit.detail.group_add",
    staff.GROUP_REMOVED: "admin.audit.detail.group_remove",
    "reason": "admin.audit.detail.reason",
    "actor": "admin.audit.detail.actor",
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

SOURCE_LABELS: dict[Source, str] = {
    Source.ADMIN: "admin.audit.source.admin",
    Source.SYSTEM: "admin.audit.source.system",
}


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


@dataclass(frozen=True)
class FilterView:
    """The filter form: the choices and what is selected now."""

    sources: tuple[Option, ...]
    source: str
    groups: tuple[Option, ...]
    group: str
    problems: bool
    booking: str
    active: bool


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
            value = count
            if count in set(Role):
                value = _(ROLE_LABELS[Role(count)])
            elif name == "reason" and count in set(ChangeReason):
                value = _(CHANGE_REASON_LABELS[ChangeReason(count)])
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


def _narrow(
    source: str | None,
    problems: str | None,
    group: str | None,
    booking: str | None,
    groups: set[str],
) -> AuditFilter:
    """Turn the query parameters into a filter; unknown or empty values are ignored."""
    return AuditFilter(
        source=Source(source) if source in set(Source) else None,
        problems_only=problems == "1",
        group=group if group in groups else None,
        booking_id=int(booking) if booking and booking.isdigit() and len(booking) <= 12 else None,
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
        )
        for entry in entries
        for e in (entry.event,)
    ]


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
    source: str | None = None,
    problems: str | None = None,
    group: str | None = None,
    booking: str | None = None,
) -> HTMLResponse:
    """Show the newest audit events and system events in scope, optionally filtered."""
    _ = locale.gettext
    service = admin_service(request)
    in_scope = await service.groups(context.principal, Permission.AUDIT_VIEW)
    narrow = _narrow(source, problems, group, booking, {g.group.slug for g in in_scope})
    events = await service.audit_log(context.principal, narrow)
    views = audit_views(request, locale, context.principal, events)
    filters = FilterView(
        sources=tuple(Option(s.value, _(SOURCE_LABELS[s])) for s in Source),
        source=narrow.source.value if narrow.source else "",
        groups=tuple(Option(g.group.slug, f"{g.group.name} ({g.group.slug})") for g in in_scope),
        group=narrow.group or "",
        problems=narrow.problems_only,
        booking=str(narrow.booking_id) if narrow.booking_id else "",
        active=narrow != AuditFilter(),
    )
    return _page(
        request,
        locale,
        "admin/audit.html",
        context.principal,
        {"events": views, "filters": filters},
    )
