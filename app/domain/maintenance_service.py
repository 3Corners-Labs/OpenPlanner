"""Use cases of maintenance mode (ADR 0038).

Three callers:

    every request   status() ─► environment? ─► on (no database read)
                              ─► cached row, at most ``cache`` old ─► on | off
    superuser       overview · switch_on · switch_off   (system.maintenance, audited)
    host            switch_on_host · switch_off_host    (CLI, the updater; audited as the actor)

``refuse`` audits a change that the matrix allowed but maintenance does not
(``maintenance.refused``, with the permission, never the form content).

``MaintenanceWatch`` runs in the worker: maintenance has no automatic end (ADR 0038 §6), so
maintenance that stays on longer than ``MAINTENANCE_ALERT_HOURS`` is recorded as a system event
(``maintenance.overdue``) and again every ``MAINTENANCE_ALERT_REPEAT_HOURS`` while it lasts.

The cache keeps the web process from reading the row on every request; a switch made in this
process clears it at once, a switch made elsewhere (the CLI, another process) is seen within
``cache``.
"""

from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Reason, Target
from app.domain.errors import DomainError, ErrorCode
from app.domain.maintenance import (
    OFF,
    OFF_DETAIL,
    ON_DETAIL,
    OVERDUE_EVENT,
    REFUSED_DETAIL,
    MaintenanceReason,
    MaintenanceState,
    MaintenanceStatus,
    overdue,
    switched_on,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.system_log import SystemLog

_PERMISSION = Permission.SYSTEM_MAINTENANCE


class MaintenanceService:
    """Application service of maintenance mode.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        environment: Whether ``MAINTENANCE_MODE`` switches maintenance on.
        cache: How long a read of the stored switch is trusted (at most 5 seconds).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        environment: bool,
        cache: timedelta,
    ) -> None:
        """Store the collaborators; nothing is cached yet."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._environment = environment
        self._cache = cache
        self._cached: tuple[datetime, MaintenanceState] | None = None

    # --- every request -------------------------------------------------------------------

    async def status(self) -> MaintenanceStatus:
        """Return whether maintenance is on, reading the stored switch at most once per ``cache``.

        With the environment override the database is not read at all: it may be the very
        thing being repaired.
        """
        if self._environment:
            return MaintenanceStatus(OFF, environment=True)
        now = self._clock.now()
        if self._cached is not None and now - self._cached[0] < self._cache:
            return MaintenanceStatus(self._cached[1])
        async with self._uow.read() as tx:
            state = await tx.get_maintenance_state()
        self._cached = (now, state)
        return MaintenanceStatus(state)

    async def refuse(self, principal: Principal, permission: Permission, target: Target) -> None:
        """Audit a change refused because maintenance is on (the permission, never the input)."""
        await self._admin.record(
            principal.subject,
            permission.value,
            Outcome.DENIED,
            Reason.MAINTENANCE.value,
            target.organisation,
            target.group,
            detail=REFUSED_DETAIL,
        )

    # --- superuser -----------------------------------------------------------------------

    async def overview(self, principal: Principal) -> MaintenanceStatus:
        """Return the current state for the maintenance page, read fresh.

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        async with self._uow.read() as tx:
            state = await tx.get_maintenance_state()
        return MaintenanceStatus(state, self._environment)

    async def switch_on(
        self,
        principal: Principal,
        reason: MaintenanceReason,
        expected_end: datetime | None,
    ) -> MaintenanceState:
        """Switch maintenance on (or change its expected end and reason) and audit it.

        Raises:
            DomainError: ``not_permitted``; ``invalid_configuration`` for an expected end
                that is not in the future.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        return await self._switch_on(principal.subject, reason, expected_end)

    async def switch_off(self, principal: Principal) -> MaintenanceState:
        """Switch the stored switch off and audit it.

        Raises:
            DomainError: ``not_permitted``; ``maintenance_from_environment`` while
                ``MAINTENANCE_MODE`` keeps it on (the portal cannot overrule the operator).
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        if self._environment:
            raise DomainError(ErrorCode.MAINTENANCE_FROM_ENVIRONMENT)
        return await self._switch_off(principal.subject)

    # --- host ----------------------------------------------------------------------------

    async def switch_on_host(self, actor: str, reason: MaintenanceReason) -> MaintenanceState:
        """Switch maintenance on from the host (CLI, updater), audited as ``actor``."""
        return await self._switch_on(actor, reason, None)

    async def switch_off_host(self, actor: str) -> MaintenanceState:
        """Switch the stored switch off from the host, audited as ``actor``.

        The environment override is the operator's own setting; it stays as it is.
        """
        return await self._switch_off(actor)

    async def stored(self) -> MaintenanceStatus:
        """Return the stored switch and the override, read fresh (CLI ``maintenance status``)."""
        async with self._uow.read() as tx:
            return MaintenanceStatus(await tx.get_maintenance_state(), self._environment)

    # --- internals -----------------------------------------------------------------------

    async def _switch_on(
        self, actor: str, reason: MaintenanceReason, expected_end: datetime | None
    ) -> MaintenanceState:
        """Store the switch on and audit ``maintenance.on``."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            state = switched_on(now, actor, reason, expected_end, await tx.get_maintenance_state())
            await tx.save_maintenance_state(state)
            await _audit(tx, now, actor, ON_DETAIL, reason.value)
        self._cached = None
        return state

    async def _switch_off(self, actor: str) -> MaintenanceState:
        """Store the switch off and audit ``maintenance.off`` (nothing to do when it is off)."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            if (await tx.get_maintenance_state()).on:
                await tx.save_maintenance_state(OFF)
                await _audit(tx, now, actor, OFF_DETAIL, "")
        self._cached = None
        return OFF


async def _audit(tx: Transaction, at: datetime, actor: str, detail: str, reason: str) -> None:
    """Record a switch: global, so only the superuser sees it in the audit log."""
    await tx.add_audit_event(
        AuditEvent(at, actor, _PERMISSION.value, Outcome.SUCCESS, reason, detail=detail)
    )


class MaintenanceWatch:
    """Reports maintenance that has been on too long, as a system event (worker).

    Args:
        uow: Reads the stored switch.
        clock: Current time.
        system_log: Where the events go (global: only the superuser sees them).
        alert_after: How long maintenance may be on before it is reported.
        repeat: How often a report is repeated while it stays on.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        system_log: SystemLog,
        *,
        alert_after: timedelta,
        repeat: timedelta,
    ) -> None:
        """Store the collaborators; nothing has been reported yet."""
        self._uow = uow
        self._clock = clock
        self._system_log = system_log
        self._alert_after = alert_after
        self._repeat = repeat
        self._reported: tuple[datetime | None, datetime] | None = None

    async def check(self) -> int | None:
        """Record the event when due; return the hours maintenance has been on, if reported.

        The environment override has no start time and is the operator's own doing, so only
        the stored switch is judged. A new window (another ``since``) is reported afresh.
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            state = await tx.get_maintenance_state()
        if not overdue(MaintenanceStatus(state), now, self._alert_after) or state.since is None:
            self._reported = None
            return None
        if self._reported is not None:
            since, at = self._reported
            if since == state.since and now - at < self._repeat:
                return None
        hours = int((now - state.since) / timedelta(hours=1))
        await self._system_log.record(
            OVERDUE_EVENT,
            Outcome.FAILURE,
            reason=state.reason.value if state.reason else "",
            detail=f"hours:{hours}",
        )
        self._reported = (state.since, now)
        return hours
