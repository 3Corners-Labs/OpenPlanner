"""Use cases of maintenance mode (ADR 0038).

Three callers:

    every request   status() ─► environment? ─► on (no database read)
                              ─► cached row, at most ``cache`` old ─► on | off
    superuser       overview · switch_on · switch_off   (system.maintenance, audited)
    host            switch_on_host · switch_off_host    (CLI, the updater; audited as the actor)

``refuse`` audits a change that the matrix allowed but maintenance does not
(``maintenance.refused``, with the permission, never the form content).

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
    REFUSED_DETAIL,
    MaintenanceReason,
    MaintenanceState,
    MaintenanceStatus,
    switched_on,
)
from app.domain.ports import Clock, Transaction, UnitOfWork

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
