"""Alerts: lasting conditions that ask an administrator to act now (ADR 0061 §7, OV-6).

An alert is not a table of its own. A watch that notices a condition records a system event
when it starts (``failure``) and when it ends (``success``); the alert is the open streak of
such a *condition event*: its latest event is a failure. The register ``ALERT_RULES`` names the
condition events, the level each reason means (``warning`` or ``outage``) and the permission
whose reach decides who sees it (ADR 0014). One derived condition has no event, because the
process that would write it is the one that stopped: the worker's heartbeat (``outage``).

    system events (newest per action, inside the reader's scope)
        ─► per key (action, organisation, group, subject): failures since the last success
        ─► Alert(level, reason, since = first failure of the streak, latest event)
    worker heartbeat older than its limit ─► Alert(outage), platform reach only

What happens with an alert is the reader's choice (``PopupChoice``): the banner shows the
outages on every admin page until they end, unless closed for that streak
(``Alert.token``); pop-ups on the overview show the levels the admin chose.

Alerts carry codes only (action, reason, slugs, a moment): never a participant's data.
"""

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.audit import AuditEvent, AuditFilter, Outcome, Source
from app.domain.authorization import (
    LEVELS,
    Level,
    Permission,
    Principal,
    Scope,
    Target,
    decide,
    scope_for,
)
from app.domain.connection_health import HEALTH_EVENT as CONNECTION_HEALTH
from app.domain.connection_health import WEBHOOKS_MISSING, HealthState
from app.domain.integration_check import CHECK_EVENT
from app.domain.log_destinations import HEALTH_EVENT
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.public_address import EVENT as ADDRESS_EVENT
from app.domain.retention_sweep import SWEPT
from app.domain.storage import FREE_SPACE_CRITICAL, STORAGE_EVENT
from app.domain.tls import EVENT as CERTIFICATE_EVENT
from app.domain.tls import Finding

QUEUE_WAIT_EVENT = "queue.wait"
"""System event of a group's longest wait crossing ``queue_wait_warning_minutes`` (OV-7)."""

QUEUE_WAIT_OVER = "wait_over_warning"
"""Reason of a ``queue.wait`` failure: the longest wait is above the warning threshold."""

WORKER_ALERT = "worker.heartbeat"
"""Action of the derived alert of a stale worker heartbeat; never stored as an event."""

WORKER_STALE = "stale"
"""Reason of the worker alert."""

EVENTS_PER_RULE = 200
"""Newest events read per condition action; enough for the latest of every key in reach."""

MAX_DISMISSED = 20
"""Closed banner alerts remembered per session; the oldest is forgotten first."""

TOKEN_LENGTH = 16
"""Hex characters of ``Alert.token``."""


class AlertLevel(StrEnum):
    """How urgent an alert is, from least to most."""

    INFO = "info"
    WARNING = "warning"
    OUTAGE = "outage"

    @property
    def rank(self) -> int:
        """Position from least (0) to most urgent."""
        return tuple(AlertLevel).index(self)


class PopupChoice(StrEnum):
    """Which alerts may pop up for an admin (ADR 0061 §7), from the profile menu."""

    ALL = "all"
    WARNINGS = "warnings"
    OUTAGES = "outages"
    OFF = "off"


_POPUP_FLOOR: dict[PopupChoice, AlertLevel | None] = {
    PopupChoice.ALL: AlertLevel.INFO,
    PopupChoice.WARNINGS: AlertLevel.WARNING,
    PopupChoice.OUTAGES: AlertLevel.OUTAGE,
    PopupChoice.OFF: None,
}


def lowest_popup(choice: str) -> AlertLevel | None:
    """Return the least urgent level that pops up under ``choice`` (``None``: nothing does).

    An unknown choice counts as the default, ``warnings``.
    """
    known = choice if choice in set(PopupChoice) else PopupChoice.WARNINGS
    return _POPUP_FLOOR[PopupChoice(known)]


@dataclass(frozen=True)
class AlertRule:
    """One condition event and what its failures mean.

    Attributes:
        action: The system event action.
        permission: Whose reach shows the alert; a group-level permission follows the event's
            group, a global one shows platform events to whoever holds it.
        outage: Reasons that make the alert an outage; any other failure is a warning.
        subject: The ``detail`` key that tells two conditions of one action apart (a log
            destination, an address); empty when the organisation and group are enough.
    """

    action: str
    permission: Permission
    outage: frozenset[str] = frozenset()
    subject: str = ""

    def level_of(self, reason: str) -> AlertLevel:
        """Return the level of a failure with ``reason``."""
        return AlertLevel.OUTAGE if reason in self.outage else AlertLevel.WARNING


ALERT_RULES: tuple[AlertRule, ...] = (
    # ADR 0040: the data volume; a full disk stops bookings and the audit log.
    AlertRule(STORAGE_EVENT, Permission.SETTINGS_PLATFORM_VIEW, frozenset({FREE_SPACE_CRITICAL})),
    # ADR 0064/0065: a certificate that browsers refuse stops every participant.
    AlertRule(
        CERTIFICATE_EVENT,
        Permission.SETTINGS_PLATFORM_VIEW,
        frozenset(
            {
                Finding.EXPIRED.value,
                Finding.NOT_YET_VALID.value,
                Finding.UNTRUSTED.value,
                Finding.NAME_MISMATCH.value,
            }
        ),
    ),
    # ADR 0086: the public or webhook address does not come back to this installation.
    AlertRule(ADDRESS_EVENT, Permission.SETTINGS_PLATFORM_VIEW, subject="address"),
    # ADR 0085: a log destination stopped accepting lines.
    AlertRule(HEALTH_EVENT, Permission.SETTINGS_PLATFORM_VIEW, subject="destination"),
    # ADR 0102: personal data past its retention could not be deleted.
    AlertRule(SWEPT, Permission.SETTINGS_PLATFORM_VIEW),
    # ADR 0065: the automatic check of a group's integration chain fails.
    AlertRule(CHECK_EVENT, Permission.AUDIT_VIEW),
    # ADR 0061 OV-7: participants wait longer than the organisation's warning threshold.
    AlertRule(QUEUE_WAIT_EVENT, Permission.QUEUE_WORK),
    # ADR 0082 point 7: a connection of the organisation is degraded (warning) or down.
    AlertRule(
        CONNECTION_HEALTH, Permission.AUDIT_VIEW, frozenset({HealthState.DOWN.value}), "platform"
    ),
    # ADR 0082 point 6: the reconcile found states the webhooks did not bring.
    AlertRule(WEBHOOKS_MISSING, Permission.AUDIT_VIEW),
)
"""Every condition event that raises an alert; a test checks that each watch writes one."""

RULES_BY_ACTION = {rule.action: rule for rule in ALERT_RULES}

WORKER_RULE = AlertRule(WORKER_ALERT, Permission.SETTINGS_PLATFORM_VIEW, frozenset({WORKER_STALE}))
"""The derived heartbeat alert: no worker means no messages, meetings or deletions."""


@dataclass(frozen=True)
class Alert:
    """One open alert.

    Attributes:
        action: The condition event's action (or ``WORKER_ALERT``).
        level: How urgent it is now (from the latest failure).
        reason: The reason code of the latest failure.
        organisation: The organisation slug, for an alert about one.
        group: The group slug, for an alert about one.
        subject: What the ``AlertRule.subject`` key named, if anything.
        since: When this streak of failures started.
        latest: When the condition was last reported.
        detail: The latest event's detail (counts and codes only).
        trace_id: The latest event's trace, for the link to its chain in the log.
    """

    action: str
    level: AlertLevel
    reason: str
    organisation: str | None
    group: str | None
    subject: str
    since: datetime
    latest: datetime
    detail: str = ""
    trace_id: str = ""

    @property
    def key(self) -> str:
        """Which condition this is, the same across its streaks."""
        return "|".join((self.action, self.organisation or "", self.group or "", self.subject))

    @property
    def token(self) -> str:
        """A short id of this streak at this level; closing the banner remembers it.

        A new streak, or the same one turning more urgent, gets a new token, so it is shown
        again.
        """
        seed = f"{self.key}|{self.since.isoformat()}|{self.level.value}"
        return hashlib.sha256(seed.encode()).hexdigest()[:TOKEN_LENGTH]


def _subject(detail: str, key: str) -> str:
    """Return the value of ``key`` in a detail such as ``kind:syslog,destination:3``."""
    if not key:
        return ""
    for part in detail.split(","):
        name, _sep, value = part.partition(":")
        if name == key:
            return value
    return ""


def open_alerts(
    events: Iterable[AuditEvent], rules: Sequence[AlertRule] = ALERT_RULES
) -> list[Alert]:
    """Return the open alerts among condition ``events``, most urgent and newest first.

    Events of other actions are ignored. Per key the events are read oldest first: a success
    ends the streak, a failure starts one or continues it. Retries and skips change nothing.
    """
    by_action = {rule.action: rule for rule in rules}
    streams: dict[tuple[str, str | None, str | None, str], list[AuditEvent]] = {}
    for event in events:
        rule = by_action.get(event.action)
        if rule is None:
            continue
        key = (event.action, event.organisation, event.group, _subject(event.detail, rule.subject))
        streams.setdefault(key, []).append(event)
    alerts = []
    for (action, organisation, group, subject), stream in streams.items():
        since: datetime | None = None
        last: AuditEvent | None = None
        for event in sorted(stream, key=lambda e: e.at):
            if event.outcome is Outcome.SUCCESS:
                since, last = None, None
            elif event.outcome is Outcome.FAILURE:
                since = since or event.at
                last = event
        if since is None or last is None:
            continue
        alerts.append(
            Alert(
                action,
                by_action[action].level_of(last.reason),
                last.reason,
                organisation,
                group,
                subject,
                since,
                last.at,
                last.detail,
                last.trace_id,
            )
        )
    return ordered(alerts)


def ordered(alerts: Iterable[Alert]) -> list[Alert]:
    """Most urgent first, then the most recently reported."""
    return sorted(alerts, key=lambda a: (-a.level.rank, -a.latest.timestamp(), a.key))


def at_least(alerts: Iterable[Alert], level: AlertLevel | None) -> list[Alert]:
    """Keep the alerts of ``level`` or more urgent; ``None`` keeps nothing."""
    if level is None:
        return []
    return [a for a in alerts if a.level.rank >= level.rank]


def banner_alerts(alerts: Iterable[Alert], dismissed: Iterable[str]) -> list[Alert]:
    """Return the outages the banner shows: every open one not closed for its streak."""
    closed = set(dismissed)
    return [a for a in alerts if a.level is AlertLevel.OUTAGE and a.token not in closed]


def remember_dismissal(dismissed: Sequence[str], token: str) -> list[str]:
    """Add ``token`` to the closed banner alerts, keeping at most ``MAX_DISMISSED``."""
    kept = [t for t in dismissed if t != token]
    return [*kept, token][-MAX_DISMISSED:]


def worker_alert(beat: datetime | None, now: datetime, max_age: timedelta) -> Alert | None:
    """Return the outage of a worker whose heartbeat is older than ``max_age``.

    No heartbeat at all is not an alert: a fresh installation, or a host without a shared
    volume, shows that as *unknown* on System status instead.
    """
    if beat is None or now - beat <= max_age:
        return None
    return Alert(WORKER_ALERT, AlertLevel.OUTAGE, WORKER_STALE, None, None, "", beat, beat)


def _visible(principal: Principal, rule: AlertRule) -> Scope | None:
    """Return the scope in which ``principal`` sees ``rule``'s alerts, or ``None`` for none."""
    if LEVELS[rule.permission] is Level.GLOBAL:
        allowed = decide(principal, rule.permission, Target.everything()).allowed
        return Scope(everything=True) if allowed else None
    scope = scope_for(principal, rule.permission)
    return None if scope.is_empty else scope


class AlertService:
    """Reads the open alerts in an admin's reach.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        heartbeat: Returns the worker's last heartbeat, or ``None`` when unknown.
        heartbeat_max_age: Returns how old the heartbeat may be before the worker counts as down.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        heartbeat: Callable[[], datetime | None],
        heartbeat_max_age: Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._heartbeat = heartbeat
        self._max_age = heartbeat_max_age

    async def open(self, principal: Principal) -> list[Alert]:
        """Return the open alerts ``principal`` may see, most urgent and newest first.

        A platform event is global, so the scope of the audit log already keeps it from anyone
        but a reader with everything in reach; a group-level rule follows the event's group.
        """
        events: list[AuditEvent] = []
        async with self._uow.read() as tx:
            for rule in ALERT_RULES:
                scope = _visible(principal, rule)
                if scope is None:
                    continue
                narrow = AuditFilter(source=Source.SYSTEM, action=rule.action)
                entries = await tx.list_audit_events(scope, EVENTS_PER_RULE, narrow)
                events.extend(entry.event for entry in entries)
        alerts = open_alerts(events)
        if _visible(principal, WORKER_RULE) is not None:
            stale = worker_alert(self._heartbeat(), self._clock.now(), self._max_age())
            alerts = ordered([*alerts, stale]) if stale else alerts
        return alerts


async def condition_events(tx: Transaction, actions: Iterable[str]) -> list[AuditEvent]:
    """Return the newest events of each condition action, in every scope (a system reader)."""
    events: list[AuditEvent] = []
    for action in actions:
        entries = await tx.list_audit_events(
            Scope(everything=True),
            EVENTS_PER_RULE,
            AuditFilter(source=Source.SYSTEM, action=action),
        )
        events.extend(entry.event for entry in entries)
    return events


def reports_since(events: Iterable[AuditEvent], alert: Alert, since: datetime) -> int:
    """Return how many failure events of ``alert``'s condition came in after ``since``."""
    rule = RULES_BY_ACTION.get(alert.action)
    subject = rule.subject if rule else ""
    return sum(
        1
        for e in events
        if e.action == alert.action
        and e.organisation == alert.organisation
        and e.group == alert.group
        and _subject(e.detail, subject) == alert.subject
        and e.outcome is Outcome.FAILURE
        and e.at > since
    )


async def open_conditions(uow: UnitOfWork, action: str) -> list[Alert]:
    """Return every open alert of one condition event, for a watch that starts.

    A watch remembers the conditions it reported in memory; after a restart it reads them
    here, so a condition that ended while the process was down is closed with a success.
    """
    async with uow.read() as tx:
        return open_alerts(await condition_events(tx, (action,)))
