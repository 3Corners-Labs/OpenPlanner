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
from app.domain.admin import ANONYMOUS, LOGIN_EVENT, LOGOUT_EVENT
from app.domain.api_access import CALL_EVENT, TOKEN_EVENT
from app.domain.audit import SYSTEM_ACTOR, AuditEntry, AuditFilter, Outcome, Source
from app.domain.authorization import (
    API_SCOPES,
    ApiScope,
    Permission,
    Principal,
    Reason,
    Target,
    decide,
)
from app.domain.storage import (
    DATABASE_TOO_LARGE,
    FREE_SPACE_CRITICAL,
    FREE_SPACE_LOW,
    STORAGE_EVENT,
)
from app.i18n import BoundGettext
from app.web.authz_labels import API_SCOPE_LABELS, OUTCOME_LABELS, PERMISSION_LABELS

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
    # Storage watch (ADR 0040): global, so only the superuser sees it.
    STORAGE_EVENT: "audit.event.storage_check",
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
    "closure.scenario.create": "admin.audit.detail.scenario_create",
    "closure.scenario.update": "admin.audit.detail.scenario_update",
    "closure.scenario.delete": "admin.audit.detail.scenario_delete",
    "closure.scenario.contacts": "admin.audit.detail.scenario_contacts",
    "closure.scenario.image": "admin.audit.detail.scenario_image",
    "closure.scenario.image.remove": "admin.audit.detail.scenario_image_remove",
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
}
"""Readable label per audit detail key (changed field names and schedule actions)."""

COUNT_LABELS: dict[str, str] = {
    "bookings": "admin.audit.detail.bookings",
    "notified": "admin.audit.detail.notified",
    "file": "admin.audit.detail.file",
    "scenario": "admin.audit.detail.scenario",
    "attempt": "admin.audit.detail.attempt",
    "free_percent": "admin.audit.detail.free_percent",
    "database_mb": "admin.audit.detail.database_mb",
}
"""Readable label per counted audit detail, e.g. ``bookings:3`` (ADR 0024)."""

REASON_LABELS: dict[str, str] = {
    Reason.NO_ROLE: "admin.audit.reason.no_role",
    Reason.NOT_PERMITTED: "admin.audit.reason.not_permitted",
    Reason.OUT_OF_SCOPE: "admin.audit.reason.out_of_scope",
    "stale": "admin.audit.reason.stale",
    "no_recipient": "admin.audit.reason.no_recipient",
    "no_booking": "admin.audit.reason.no_booking",
    "channel_not_configured": "admin.audit.reason.channel_not_configured",
    FREE_SPACE_LOW: "admin.audit.reason.free_space_low",
    FREE_SPACE_CRITICAL: "admin.audit.reason.free_space_critical",
    DATABASE_TOO_LARGE: "admin.audit.reason.database_too_large",
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
    views = [
        AuditView(
            admin_moment(request, locale, e.at),
            _actor_label(entry, _),
            _action_label(e.action, _),
            _(OUTCOME_LABELS[e.outcome]),
            " / ".join(t for t in (e.organisation, e.group) if t) or "–",
            OUTCOME_TONES[e.outcome],
            _detail_label(e.detail, _),
            _reason_label(e.reason, _),
            e.reason,
            e.booking_id,
            _booking_url(context.principal, entry),
        )
        for entry in events
        for e in (entry.event,)
    ]
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
