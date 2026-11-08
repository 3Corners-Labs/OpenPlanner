"""Alerts delivered outside the portal: targets, repeat and dedupe, retry (ADR 0082 point 9).

The alert itself is ADR 0061's: the open streak of a condition event (``app.domain.alerts``).
This module sends it to *alert targets*: an email address or a Webex space, per scope (the
platform's targets get the platform's alerts, an organisation's targets its own), each with a
minimum level.

    open alerts (all rules, all scopes) ─► per target in reach, at its level or above
        ─► never sent this streak, or ``alert_repeat_hours`` since the last send ─► send
        ─► else counted (``suppressed``) and named in the next message
        ─► a failed send is retried after 1, 5 and 15 minutes, then waits for the repeat;
           each failure is the system event ``alert.delivery_failed``, never an alert itself

What goes out holds codes only: level, source action, reason, organisation and group slugs,
counts, the version and a link into the portal (ADR 0082 point 10). Never a participant's data.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.admin import AdminService, record_change, require
from app.domain.alerts import ALERT_RULES, Alert, AlertLevel, open_alerts
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, AuditFilter, Outcome, Source
from app.domain.authorization import Permission, Principal, Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.alert_model import (
    AlertChannelKind,
    AlertTarget,
    DeliveryState,
    RenderedAlert,
)
from app.domain.ports import AlertSenders, Clock, IntegrationError, Transaction, UnitOfWork
from app.domain.tunable import value_of

DELIVERY_FAILED = "alert.delivery_failed"
"""System event of a failed delivery; deliberately not an alert rule (no loop)."""
TEST_ALERT = "test"
"""``Alert.action`` of a test alert sent from the portal."""
MAX_TARGETS = 10
"""Targets per scope at most (ADR 0082 *Settings*); a fixed bound, not a setting."""
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
"""Waits before the retries of a failed delivery, as for jobs (ADR 0068)."""
EVENTS_PER_RULE = 200
"""Newest events read per condition action, as ``alerts.EVENTS_PER_RULE``."""


def takes(target: AlertTarget, alert: Alert) -> bool:
    """Whether ``target`` gets ``alert``: its level is at least the target's minimum."""
    return alert.level.rank >= AlertLevel(target.min_level).rank


def due(
    alert: Alert, state: DeliveryState | None, now: datetime, repeat: timedelta
) -> bool:
    """Return whether ``alert`` must be sent now to the target ``state`` belongs to.

    A new streak or a higher level (a new token) is sent at once; the same one again only after
    ``repeat``. A failed delivery waits for its ``retry_at``; after the last retry it waits for
    the repeat, counted from the first failure's moment.
    """
    if state is None:
        return True
    if state.retry_at is not None:
        if state.attempts > len(RETRY_AFTER):
            return state.sent_at is None or now - state.sent_at >= repeat
        return now >= state.retry_at
    if state.token != alert.token:
        return True
    return state.sent_at is not None and now - state.sent_at >= repeat


def after_failure(state: DeliveryState, alert: Alert, now: datetime) -> DeliveryState:
    """Return the state after a failed try: the next retry, or none left until the repeat."""
    attempts = state.attempts + 1
    wait = RETRY_AFTER[min(attempts, len(RETRY_AFTER)) - 1]
    return DeliveryState(
        state.target_id,
        state.key,
        state.token,
        state.sent_at if attempts <= len(RETRY_AFTER) else now,
        state.suppressed,
        attempts,
        now + wait,
    )


def test_alert(now: datetime, organisation: str | None) -> Alert:
    """Return the alert a *Send a test alert* button sends: a warning with no condition."""
    return Alert(TEST_ALERT, AlertLevel.WARNING, TEST_ALERT, organisation, None, "", now, now)


class AlertNotifier:
    """Sends the open alerts to their targets; the worker runs it every minute.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        senders: The sender of a target (``AlertSenders``); ``None`` when it has none.
        compose: Writes an alert out for a target, in the target's organisation's locale.
        repeat: How long before a lasting alert is sent again (``alert_repeat_hours``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        senders: AlertSenders,
        compose: Callable[[Alert, AlertTarget, int], RenderedAlert],
        repeat: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._senders = senders
        self._compose = compose
        self._repeat = repeat

    async def run(self) -> int:
        """Deliver what is due; return how many messages went out."""
        now = self._clock.now()
        repeat = value_of(self._repeat)
        async with self._uow.read() as tx:
            alerts = await _open(tx)
            targets = await tx.all_alert_targets()
            organisations = {o.id: o.slug for o in await tx.list_organisations(Scope(True))}
            states = {(s.target_id, s.key): s for s in await tx.list_alert_deliveries()}
        sent = 0
        for target in targets:
            slug = organisations.get(target.organisation_id) if target.organisation_id else None
            for alert in alerts:
                if alert.organisation != slug or not takes(target, alert):
                    continue
                state = states.get((_id(target), alert.key))
                if not due(alert, state, now, repeat):
                    if state is not None and state.token == alert.token and state.retry_at is None:
                        await self._count(state)
                    continue
                sent += await self._deliver(target, alert, state, now)
        return sent

    async def _count(self, state: DeliveryState) -> None:
        """Count a report of an alert that was already sent (dedupe, ADR 0082 point 9)."""
        async with self._uow.locked() as tx:
            await tx.save_alert_delivery(
                DeliveryState(
                    state.target_id,
                    state.key,
                    state.token,
                    state.sent_at,
                    state.suppressed + 1,
                    state.attempts,
                    state.retry_at,
                )
            )

    async def _deliver(
        self, target: AlertTarget, alert: Alert, state: DeliveryState | None, now: datetime
    ) -> int:
        """Send ``alert`` to ``target`` and store the outcome; return 1 when it went out."""
        suppressed = state.suppressed if state is not None else 0
        error = await send(self._senders, target, self._compose(alert, target, suppressed))
        base = state or DeliveryState(_id(target), alert.key, alert.token)
        async with self._uow.locked() as tx:
            if error is None:
                await tx.save_alert_delivery(
                    DeliveryState(_id(target), alert.key, alert.token, now)
                )
                return 1
            await tx.save_alert_delivery(after_failure(base, alert, now))
            await tx.add_audit_event(_failure(target, alert, error, now))
        return 0


async def send(senders: AlertSenders, target: AlertTarget, message: RenderedAlert) -> str | None:
    """Send ``message`` to ``target``; return ``None`` on success, else the error code.

    ``no_sender`` when the target's channel is not set up (no email channel, no bot token).
    """
    sender = await senders.sender(target)
    if sender is None:
        return "no_sender"
    try:
        await sender.send(target.address, message)
    except IntegrationError as error:
        return error.code
    return None


class AlertTargetService:
    """Application service for the alert targets of the platform and of an organisation.

    The platform's targets need ``settings.platform.manage``, an organisation's
    ``integrations.manage`` on it (ADR 0082 *Settings*); every change and every test alert is
    audited with the target's id and kind, never its address.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits refusals.
        senders: The sender of a target, for the test alert.
        compose: Writes the test alert out.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        senders: AlertSenders,
        compose: Callable[[Alert, AlertTarget, int], RenderedAlert],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._senders = senders
        self._compose = compose

    async def targets(self, principal: Principal, organisation: str | None) -> list[AlertTarget]:
        """Return the targets of the platform (``None``) or of ``organisation``.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        organisation_id, _ = await self._authorize(principal, organisation)
        async with self._uow.read() as tx:
            return await tx.list_alert_targets(organisation_id)

    async def add(self, principal: Principal, organisation: str | None, target: AlertTarget) -> int:
        """Store ``target`` for the platform or ``organisation``; at most ``MAX_TARGETS``.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` naming ``targets`` (limit reached).
        """
        organisation_id, permission = await self._authorize(principal, organisation)
        async with self._uow.locked() as tx:
            if len(await tx.list_alert_targets(organisation_id)) >= MAX_TARGETS:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "targets")
            stored = AlertTarget(
                None, organisation_id, target.kind, target.address, target.min_level
            )
            target_id = await tx.add_alert_target(stored)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                permission,
                organisation,
                None,
                f"alert_target.add,id:{target_id},kind:{target.kind.value}",
            )
        return target_id

    async def remove(self, principal: Principal, organisation: str | None, target_id: int) -> None:
        """Remove one target of the platform or ``organisation``.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or ``invalid_configuration``
                naming ``target`` (not one of this scope).
        """
        organisation_id, permission = await self._authorize(principal, organisation)
        async with self._uow.locked() as tx:
            await self._target(tx, organisation_id, target_id)
            await tx.delete_alert_target(target_id)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                permission,
                organisation,
                None,
                f"alert_target.remove,id:{target_id}",
            )

    async def send_test(
        self, principal: Principal, organisation: str | None, target_id: int
    ) -> str | None:
        """Send a test alert to one target now; return ``None`` or the error code.

        The outcome is audited (``alert_target.test``) with the target's id and the code.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` naming ``target``.
        """
        organisation_id, permission = await self._authorize(principal, organisation)
        async with self._uow.read() as tx:
            target = await self._target(tx, organisation_id, target_id)
        now = self._clock.now()
        error = await send(
            self._senders, target, self._compose(test_alert(now, organisation), target, 0)
        )
        async with self._uow.locked() as tx:
            await record_change(
                tx,
                now,
                principal,
                permission,
                organisation,
                None,
                f"alert_target.test,id:{target_id},outcome:{error or 'sent'}",
            )
        return error

    async def _authorize(
        self, principal: Principal, organisation: str | None
    ) -> tuple[int | None, Permission]:
        """Authorise for the scope; return the organisation's id (``None``) and the permission."""
        if organisation is None:
            require(await self._admin.authorize(principal, Permission.SETTINGS_PLATFORM_MANAGE))
            return None, Permission.SETTINGS_PLATFORM_MANAGE
        require(
            await self._admin.authorize(
                principal, Permission.INTEGRATIONS_MANAGE, organisation_slug=organisation
            )
        )
        async with self._uow.read() as tx:
            found = await tx.get_organisation(organisation)
        if found is None:
            raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
        return found.id, Permission.INTEGRATIONS_MANAGE

    async def _target(
        self, tx: Transaction, organisation_id: int | None, target_id: int
    ) -> AlertTarget:
        """Return the target ``target_id`` of the scope, or refuse naming ``target``."""
        for target in await tx.list_alert_targets(organisation_id):
            if target.id == target_id:
                return target
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "target")


async def _open(tx: Transaction) -> list[Alert]:
    """Return every open alert of every rule, in all scopes (the worker sees everything)."""
    events: list[AuditEvent] = []
    for rule in ALERT_RULES:
        entries = await tx.list_audit_events(
            Scope(True), EVENTS_PER_RULE, AuditFilter(source=Source.SYSTEM, action=rule.action)
        )
        events += [entry.event for entry in entries]
    return open_alerts(events)


def _id(target: AlertTarget) -> int:
    """Return a stored target's id."""
    if target.id is None:  # pragma: no cover - targets are read from the database
        raise ValueError("target is not stored")
    return target.id


def _failure(target: AlertTarget, alert: Alert, error: str, now: datetime) -> AuditEvent:
    """Return the system event of a failed delivery (codes only; never an alert itself)."""
    return AuditEvent(
        now,
        SYSTEM_ACTOR,
        DELIVERY_FAILED,
        Outcome.FAILURE,
        error[:64],
        alert.organisation,
        alert.group,
        f"target:{_id(target)},kind:{target.kind.value},alert:{alert.action}",
        Source.SYSTEM,
    )
