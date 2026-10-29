"""Diagnostic mode: debug events in the audit log for one group or organisation (ADR 0040).

Normally the audit log holds no debug detail. The superuser (product owner, 2026-10-29) can
switch diagnostic mode on for one group or one whole organisation; until it ends, the
platform writes debug events (category ``diagnostic``, level ``debug``) about that target,
and the group's admins see them in their own audit log. The mode ends by itself after
``DIAGNOSTIC_MODE_HOURS``; switching it on or off is audited. At storage grade ``critical`` the
worker switches every mode off (the emergency brake), and debug events stop with it.

    superuser ─► switch_on(group) ─► diagnostic_modes row (until now + hours) ─► audited
    worker / web ─► DiagnosticLog.debug(...) ─► mode active for the group or its org?
                                            ─► yes: AuditEvent(category=diagnostic, debug)
    worker, grade critical ─► brake() ─► every mode ends ─► system event diagnostics.brake

Debug events carry codes and ids only, like every audit event, and never leave the
installation (ADR 0034).
"""

from collections.abc import Callable
from datetime import timedelta

from app.domain.admin import AdminService, record_change, require
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Category, Level, Outcome, Source
from app.domain.authorization import Permission, Principal
from app.domain.diagnostic_mode import DiagnosticMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UnitOfWork
from app.domain.tunable import value_of

BRAKE_EVENT = "diagnostics.brake"
"""System event when the worker ends diagnostic mode because the disk is nearly full."""


class DiagnosticsService:
    """Switches diagnostic mode on and off; the superuser only.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Decides and audits the permission.
        duration: How long a mode lasts (``DIAGNOSTIC_MODE_HOURS``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        duration: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._duration = duration

    async def active(self) -> list[DiagnosticMode]:
        """Return the modes that have not ended."""
        async with self._uow.read() as tx:
            return await tx.active_diagnostic_modes(self._clock.now())

    async def switch_on(
        self, principal: Principal, organisation: str, group: str | None = None
    ) -> DiagnosticMode:
        """Start (or restart) diagnostic mode for a group or a whole organisation.

        Raises:
            DomainError: ``not_permitted`` without ``diagnostics.manage``;
                ``organisation_not_found`` or ``group_not_found`` for an unknown target, or a
                group that is not one of the organisation's.
        """
        require(await self._admin.authorize(principal, Permission.DIAGNOSTICS_MANAGE))
        now = self._clock.now()
        async with self._uow.locked() as tx:
            if group is not None:
                summary = await tx.get_group_summary(group)
                if summary is None or summary.organisation_slug != organisation:
                    raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            elif await tx.get_organisation(organisation) is None:
                raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
            mode = DiagnosticMode(
                organisation, group, now + value_of(self._duration), principal.subject
            )
            await tx.save_diagnostic_mode(mode)
            await record_change(
                tx,
                now,
                principal,
                Permission.DIAGNOSTICS_MANAGE,
                organisation,
                group,
                "diagnostics.on",
            )
        return mode

    async def switch_off(
        self, principal: Principal, organisation: str, group: str | None = None
    ) -> None:
        """End diagnostic mode for a target; ending one that is not on changes nothing.

        Raises:
            DomainError: ``not_permitted`` without ``diagnostics.manage``.
        """
        require(await self._admin.authorize(principal, Permission.DIAGNOSTICS_MANAGE))
        now = self._clock.now()
        async with self._uow.locked() as tx:
            if await tx.end_diagnostic_mode(organisation, group, now):
                await record_change(
                    tx,
                    now,
                    principal,
                    Permission.DIAGNOSTICS_MANAGE,
                    organisation,
                    group,
                    "diagnostics.off",
                )

    async def brake(self, reason: str) -> int:
        """End every mode because storage is critical; return how many ended (worker only)."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            ended = await tx.end_all_diagnostic_modes(now)
            if ended:
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        SYSTEM_ACTOR,
                        BRAKE_EVENT,
                        Outcome.SUCCESS,
                        reason,
                        detail=f"modes:{ended}",
                        source=Source.SYSTEM,
                    )
                )
        return ended


class DiagnosticLog:
    """Writes debug events, only for targets in diagnostic mode.

    Args:
        uow: Opens database transactions.
        clock: Current time.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

    async def debug(
        self,
        action: str,
        organisation: str | None,
        group: str | None,
        *,
        booking_id: int | None = None,
        detail: str = "",
    ) -> bool:
        """Write a debug event when a mode covers the target; return whether it was written.

        Args:
            action: Event key, e.g. ``job.run``.
            organisation: Organisation slug of what the event is about.
            group: Group slug of what the event is about.
            booking_id: The booking concerned, if any.
            detail: Machine keys, e.g. ``type:send_reminder,ms:120``; never values.
        """
        if organisation is None:
            return False
        now = self._clock.now()
        async with self._uow.locked() as tx:
            modes = await tx.active_diagnostic_modes(now)
            if not any(mode.covers(organisation, group) for mode in modes):
                return False
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    SYSTEM_ACTOR,
                    action,
                    Outcome.SUCCESS,
                    organisation=organisation,
                    group=group,
                    detail=detail,
                    source=Source.SYSTEM,
                    booking_id=booking_id,
                    category=Category.DIAGNOSTIC,
                    level=Level.DEBUG,
                )
            )
        return True
