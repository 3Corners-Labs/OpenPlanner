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

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.admin import AdminService, record_change, require
from app.domain.alert_model import (
    AlertChannelKind,
    AlertTarget,
    DeliveryState,
    RenderedAlert,
)
from app.domain.alerts import (
    ALERT_RULES,
    Alert,
    AlertLevel,
    condition_events,
    open_alerts,
    reports_since,
)
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal, Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import BACKOFF
from app.domain.ports import (
    AlertSenders,
    BotTokenStore,
    Clock,
    IntegrationError,
    Transaction,
    UnitOfWork,
)
from app.domain.tunable import value_of

DELIVERY_FAILED = "alert.delivery_failed"
"""System event of a failed delivery; deliberately not an alert rule (no loop)."""
DEFAULT_LOCALE = "nl"
"""The locale of the platform's alerts (CLAUDE.md: Dutch by default)."""
TEST_ALERT = "test"
"""``Alert.action`` of a test alert sent from the portal."""
MAX_TARGETS = 10
"""Targets per scope at most (ADR 0082 *Settings*); a fixed bound, not a setting."""
BOT_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{20,400}$")
"""A Webex bot's access token: letters, digits, ``_``, ``.``, ``-``; never logged or shown."""
RETRY_AFTER = BACKOFF
"""Waits before the retries of a failed delivery: the jobs' backoff (ADR 0068); after the last
retry the delivery waits for the repeat interval."""
NOTIFY_EVERY = timedelta(minutes=1)
"""How often the worker delivers due alerts; fixed, the repeat is the setting."""


def takes(target: AlertTarget, alert: Alert) -> bool:
    """Whether ``target`` gets ``alert``: its level is at least the target's minimum."""
    return alert.level.rank >= AlertLevel(target.min_level).rank


class Action(StrEnum):
    """What the notifier does with one alert for one target now."""

    SEND = "send"
    WAIT = "wait"


def step(alert: Alert, state: DeliveryState | None, now: datetime, repeat: timedelta) -> Action:
    """Return whether ``alert`` is sent to the target ``state`` belongs to, now (the one rule).

    A new streak or a higher level (a new token) goes out at once. The same one again only
    after ``repeat`` since it was last sent. A failed delivery waits for its ``retry_at``.
    """
    if state is None:
        return Action.SEND
    if state.retry_at is not None:
        return Action.SEND if now >= state.retry_at else Action.WAIT
    if state.token != alert.token:
        return Action.SEND
    if state.sent_at is not None and now - state.sent_at >= repeat:
        return Action.SEND
    return Action.WAIT


def outcome(
    state: DeliveryState, alert: Alert, sent: bool, now: datetime, repeat: timedelta
) -> DeliveryState:
    """Return the state after a try: sent, the next retry, or (retries used up) the repeat."""
    if sent:
        return replace(state, token=alert.token, sent_at=now, attempts=0, retry_at=None)
    attempts = state.attempts + 1
    wait = RETRY_AFTER[attempts - 1] if attempts <= len(RETRY_AFTER) else repeat
    return replace(state, token=alert.token, attempts=attempts, retry_at=now + wait)


def trial_alert(now: datetime, organisation: str | None) -> Alert:
    """Return the alert a *Send a test alert* button sends: a warning with no condition."""
    return Alert(TEST_ALERT, AlertLevel.WARNING, TEST_ALERT, organisation, None, "", now, now)


class AlertNotifier:
    """Sends the open alerts to their targets, at most every ``NOTIFY_EVERY``.

    The worker asks it on every loop; it decides itself when a run is due, like the watches.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        senders: The sender of a target (``AlertSenders``); ``None`` when it has none.
        compose: Writes an alert out: ``compose(alert, target, reports, locale)``, in the
            default locale of the target's organisation (Dutch for the platform's), ``reports``
            being the condition's reports since the previous message.
        repeat: How long before a lasting alert is sent again (``alert_repeat_hours``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        senders: AlertSenders,
        compose: Callable[[Alert, AlertTarget, int, str], RenderedAlert],
        repeat: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators; the first run is on the first ask."""
        self._uow = uow
        self._clock = clock
        self._senders = senders
        self._compose = compose
        self._repeat = repeat
        self._ran_at: datetime | None = None

    async def run(self) -> int | None:
        """Deliver what is due when a run is due; return how many went out, ``None`` if not."""
        now = self._clock.now()
        if self._ran_at is not None and now - self._ran_at < NOTIFY_EVERY:
            return None
        self._ran_at = now
        repeat = value_of(self._repeat)
        async with self._uow.read() as tx:
            events = await condition_events(tx, (rule.action for rule in ALERT_RULES))
            targets = await tx.all_alert_targets()
            organisations = {o.id: o for o in await tx.list_organisations(Scope(True))}
            states = {(s.target_id, s.key): s for s in await tx.alert_delivery_states()}
        alerts = open_alerts(events)
        sent = 0
        for target in targets:
            owner = organisations.get(target.organisation_id) if target.organisation_id else None
            if target.organisation_id is not None and owner is None:
                continue
            slug = owner.slug if owner else None
            locale = owner.default_locale if owner else DEFAULT_LOCALE
            for alert in alerts:
                if alert.organisation != slug or not takes(target, alert):
                    continue
                state = states.get((_id(target), alert.key))
                if step(alert, state, now, repeat) is Action.WAIT:
                    continue
                since = state.sent_at if state and state.sent_at else alert.since
                reports = reports_since(events, alert, since) if state and state.sent_at else 0
                sent += await self._deliver(target, alert, state, now, repeat, locale, reports)
        return sent

    async def _deliver(
        self,
        target: AlertTarget,
        alert: Alert,
        state: DeliveryState | None,
        now: datetime,
        repeat: timedelta,
        locale: str,
        reports: int,
    ) -> int:
        """Send ``alert`` to ``target`` and store the outcome; return 1 when it went out."""
        error = await send(self._senders, target, self._compose(alert, target, reports, locale))
        base = state or DeliveryState(_id(target), alert.key, alert.token)
        async with self._uow.locked() as tx:
            await tx.save_alert_delivery(outcome(base, alert, error is None, now, repeat))
            if error is not None:
                await tx.add_audit_event(_failure(target, alert, error, now))
        return 1 if error is None else 0


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
        bots: The sealed bot tokens; ``None`` without a settings key (no secrets kept).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        senders: AlertSenders,
        compose: Callable[[Alert, AlertTarget, int, str], RenderedAlert],
        bots: BotTokenStore | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._senders = senders
        self._compose = compose
        self._bots = bots

    async def has_bot(self, principal: Principal, organisation: str | None) -> bool | None:
        """Return whether the scope has a bot token; ``None`` when none can be kept.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        organisation_id, _ = await self._authorize(principal, organisation)
        return await self._bots.has_token(organisation_id) if self._bots else None

    async def set_bot(
        self, principal: Principal, organisation: str | None, token: str | None
    ) -> None:
        """Store or, with ``None``, remove the scope's bot token; audited without the token.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``integration_not_ready``
                (no settings key) or ``invalid_configuration`` naming ``bot_token``.
        """
        organisation_id, permission = await self._authorize(principal, organisation)
        if self._bots is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
        if token is not None and not BOT_TOKEN.fullmatch(token):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "bot_token")
        now = self._clock.now()
        # The audit event first: a token that changed without one is worse than an event of a
        # change that then failed (and the admin sees the failure).
        async with self._uow.locked() as tx:
            action = "set" if token else "remove"
            await record_change(
                tx, now, principal, permission, organisation, None, f"alert_bot.{action}"
            )
        await self._bots.save(organisation_id, token, now)

    async def targets(self, principal: Principal, organisation: str | None) -> list[AlertTarget]:
        """Return the targets of the platform (``None``) or of ``organisation``.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        organisation_id, _ = await self._authorize(principal, organisation)
        async with self._uow.read() as tx:
            return await tx.alert_targets_of(organisation_id)

    async def add(
        self,
        principal: Principal,
        organisation: str | None,
        kind: str,
        address: str,
        min_level: str,
    ) -> int:
        """Store a target for the platform or ``organisation``; at most ``MAX_TARGETS``.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` naming ``kind``, ``address``, ``min_level`` or
                ``targets`` (limit reached).
        """
        organisation_id, permission = await self._authorize(principal, organisation)
        if kind not in {k.value for k in AlertChannelKind}:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "kind")
        target = AlertTarget(None, organisation_id, AlertChannelKind(kind), address, min_level)
        async with self._uow.locked() as tx:
            if len(await tx.alert_targets_of(organisation_id)) >= MAX_TARGETS:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "targets")
            target_id = await tx.add_alert_target(target)
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
            owner = await tx.get_organisation(organisation) if organisation else None
        now = self._clock.now()
        locale = owner.default_locale if owner else DEFAULT_LOCALE
        message = self._compose(trial_alert(now, organisation), target, 0, locale)
        error = await send(self._senders, target, message)
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
        for target in await tx.alert_targets_of(organisation_id):
            if target.id == target_id:
                return target
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "target")


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
