"""Alerts in the portal: the banner on every admin page and the words of each alert (ADR 0061 §7).

``load_alerts`` runs for every admin page (like the update banner, ADR 0029): it reads the open
alerts in the admin's reach once per request and keeps them on ``request.state``, where the
layout draws the outages as a banner and the overview's *Alerts* widget lists them all. A
fragment poll does not read them twice: the widget asks for itself.

    GET /admin/alerts/close?token=…&next=…   dashboard.view   close one banner alert; 303 to next

Closing is a GET for the same reason as the preferences: it changes only the admin's own session,
so maintenance (ADR 0038) must not refuse it. The banner comes back for a new streak of the same
condition, or when it turns more urgent (``Alert.token``).

An alert links to the chain of the log entry it came from when the admin may read the log there
(or to the system problems of the log, for an event outside a trace), to the queue for a wait
alert, and to *System status* for the worker. The words come from the
labels the log and *System status* already use; a reason without one gets a general sentence.
No alert holds a participant's data (ADR 0061 §8).
"""

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request
from starlette.responses import RedirectResponse

from app.admin.address_view import FINDING_LABELS as ADDRESS_FINDING_LABELS
from app.admin.audit_routes import REASON_LABELS
from app.admin.authz import AdminContext, RequirePermission, session_principal
from app.admin.forms import safe_next
from app.admin.pages import admin_moment
from app.admin.session import close_alert, session_alert_prefs, session_closed_alerts
from app.admin.top_bar import COUNTS_ATTRIBUTE
from app.domain.alerts import (
    QUEUE_WAIT_EVENT,
    WORKER_ALERT,
    Alert,
    AlertLevel,
    AlertService,
    banner_alerts,
)
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.integration_check import CHECK_EVENT
from app.domain.log_destinations import HEALTH_EVENT
from app.domain.public_address import EVENT as ADDRESS_EVENT
from app.domain.retention_sweep import SWEPT
from app.domain.storage import STORAGE_EVENT
from app.domain.tls import EVENT as CERTIFICATE_EVENT
from app.web.check_labels import FINDING_LABELS as CHECK_FINDING_LABELS
from app.web.locale import RequestLocale, resolve_locale
from app.web.settings_labels import TLS_FINDING_LABELS
from app.web.templates import FragmentResponse

log = structlog.get_logger()

router = APIRouter(prefix="/admin", include_in_schema=False)

ALERTS_ATTRIBUTE = "alerts"
"""Name on ``request.state`` under which the open alerts of this request are kept."""

ALERTS_LINK = "top.alerts"
"""The top bar link that carries the number of open alerts (SH-2)."""

STATUS_PATH = "/admin/settings"
"""Where *System status* lives; the worker alert links there."""

SOURCE_LABELS: dict[str, str] = {
    STORAGE_EVENT: "admin.alerts.source.storage",
    CERTIFICATE_EVENT: "admin.alerts.source.certificate",
    ADDRESS_EVENT: "admin.alerts.source.address",
    HEALTH_EVENT: "admin.alerts.source.log_destination",
    SWEPT: "admin.alerts.source.retention",
    CHECK_EVENT: "admin.alerts.source.integration",
    QUEUE_WAIT_EVENT: "admin.alerts.source.queue_wait",
    WORKER_ALERT: "admin.alerts.source.worker",
}
"""What an alert is about, per action; the group-level ones name the group."""

LEVEL_LABELS: dict[AlertLevel, tuple[str, str]] = {
    AlertLevel.INFO: ("admin.alerts.level.info", "accent"),
    AlertLevel.WARNING: ("admin.alerts.level.warning", "warning"),
    AlertLevel.OUTAGE: ("admin.alerts.level.outage", "danger"),
}
"""Label and tone per level; the label is always shown, so colour is never the only signal."""

OWN_TEXTS: dict[str, str] = {
    QUEUE_WAIT_EVENT: "admin.alerts.text.queue_wait",
    WORKER_ALERT: "admin.alerts.text.worker",
    HEALTH_EVENT: "admin.alerts.text.log_destination",
    SWEPT: "admin.alerts.text.retention",
}
"""Actions whose sentence is the alert's own rather than the reason's label."""

OTHER_TEXT = "admin.alerts.text.other"
"""The sentence for a reason without a label (a code from a newer check)."""


@dataclass(frozen=True)
class AlertView:
    """One alert as the banner, the widget and a pop-up show it.

    Attributes:
        token: The streak's token; the close link and the pop-up script use it.
        level: The level's value (``info``, ``warning``, ``outage``), for the pop-up filter.
        level_label: The level in words.
        tone: The chip tone that repeats the level.
        title: What it is about.
        text: What is wrong, in a sentence.
        since: Since when, in the admin's time zone.
        href: Where to act or read more; ``None`` when the admin may open nothing.
    """

    token: str
    level: str
    level_label: str
    tone: str
    title: str
    text: str
    since: str
    href: str | None


def alert_service(request: Request) -> AlertService:
    """Return the application's alert service."""
    service: AlertService = request.app.state.alert_service
    return service


def _reason_key(alert: Alert) -> str | None:
    """Return the catalogue key that explains the alert's reason, if there is one."""
    if alert.action in OWN_TEXTS:
        return OWN_TEXTS[alert.action]
    labels: dict[str, str] = {
        STORAGE_EVENT: REASON_LABELS,
        CERTIFICATE_EVENT: {f.value: key for f, key in TLS_FINDING_LABELS.items()},
        ADDRESS_EVENT: {f.value: key for f, key in ADDRESS_FINDING_LABELS.items()},
        CHECK_EVENT: CHECK_FINDING_LABELS,
    }.get(alert.action, {})
    return labels.get(alert.reason)


def _minutes(detail: str) -> int:
    """Return the ``minutes:N`` count of a wait alert's detail (0 when absent)."""
    for part in detail.split(","):
        name, _sep, value = part.partition(":")
        if name == "minutes" and value.isdigit():
            return int(value)
    return 0


def _href(principal: Principal, alert: Alert) -> str | None:
    """Return where the alert leads the admin, decided by the matrix."""
    if alert.action == WORKER_ALERT:
        return STATUS_PATH
    if alert.action == QUEUE_WAIT_EVENT and alert.group:
        return f"/admin/groups/{alert.group}/queue"
    log_scope = scope_for(principal, Permission.AUDIT_VIEW)
    visible = (
        log_scope.everything
        if alert.organisation is None
        else log_scope.contains(alert.organisation, alert.group)
    )
    if not visible:
        return None
    if alert.trace_id:
        return f"/admin/audit?trace={alert.trace_id}"
    # An event written outside a trace: the system problems, at the group when it has one.
    narrow = {
        "source": "system",
        "problems": "1",
        **({"group": alert.group} if alert.group else {}),
    }
    return f"/admin/audit?{urlencode(narrow)}"


def alert_views(
    request: Request, locale: RequestLocale, principal: Principal, alerts: list[Alert]
) -> list[AlertView]:
    """Write ``alerts`` out for the templates, in their order."""
    _ = locale.gettext
    views = []
    for alert in alerts:
        level_key, tone = LEVEL_LABELS[alert.level]
        title = _(SOURCE_LABELS.get(alert.action, OTHER_TEXT), group=alert.group or "")
        key = _reason_key(alert)
        text = (
            _(
                key,
                minutes=_minutes(alert.detail),
                time=admin_moment(request, locale, alert.latest),
            )
            if key
            else _(OTHER_TEXT, code=alert.reason or "-")
        )
        views.append(
            AlertView(
                alert.token,
                alert.level.value,
                _(level_key),
                tone,
                title,
                text,
                _("admin.alerts.since", time=admin_moment(request, locale, alert.since)),
                _href(principal, alert),
            )
        )
    return views


def request_alerts(request: Request) -> list[Alert] | None:
    """Return the open alerts ``load_alerts`` read for this request, or ``None`` if it did not."""
    found: list[Alert] | None = getattr(request.state, ALERTS_ATTRIBUTE, None)
    return found


async def read_alerts(request: Request, principal: Principal) -> list[Alert]:
    """Return the open alerts of ``principal``, read once per request."""
    found = request_alerts(request)
    if found is None:
        found = await alert_service(request).open(principal)
        setattr(request.state, ALERTS_ATTRIBUTE, found)
    return found


async def load_alerts(request: Request) -> None:
    """Read the open alerts for an admin page and put the banner on ``request.state``.

    Nothing for visitors, for a fragment or a form post (their page reads them), and nothing
    when the read fails: a full disk must not take the portal down with it, so the failure is
    logged and the page shows without a banner.
    """
    principal = session_principal(request)
    route = request.scope.get("route")
    if (
        principal is None
        or request.method != "GET"
        or getattr(route, "response_class", None) is FragmentResponse
        or not hasattr(request.app.state, "alert_service")
    ):
        return
    try:
        alerts = await read_alerts(request, principal)
    except Exception as exc:  # the database may be full or away; the page still shows
        log.warning("alerts.read_failed", error=type(exc).__name__)
        return
    counts = getattr(request.state, COUNTS_ATTRIBUTE, None)
    if isinstance(counts, dict):
        counts[ALERTS_LINK] = len(alerts)
    if not session_alert_prefs(request).banner:
        return
    shown = banner_alerts(alerts, session_closed_alerts(request))
    if shown:
        locale = resolve_locale(request)
        request.state.alert_banner = alert_views(request, locale, principal, shown)


Member = Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))]


@router.get("/alerts/close", response_model=None)
async def close(request: Request, _: Member) -> RedirectResponse:
    """Close one banner alert for this session and return to the page it was on."""
    query = request.query_params
    close_alert(request, query.get("token"))
    return RedirectResponse(safe_next(query.get("next")), status_code=303)
