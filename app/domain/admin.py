"""Admin use cases: authorise, audit and list, all through the matrix (ADR 0014).

Routes never decide on their own: they ask ``authorize`` and show what ``groups`` or
``audit_log`` return. Both the decision and the listing come from the same matrix, so they
cannot disagree.

    authorize(principal, permission, target)
        ├─ GROUP level: look up the group and its organisation (unknown → group_not_found)
        ├─ decide() ──► allowed  ──► return
        └─────────────► denied   ──► audit event (denied, reason) ──► return
"""

from dataclasses import dataclass

from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import (
    LEVELS,
    Decision,
    Level,
    Permission,
    Principal,
    Reason,
    Target,
    decide,
    scope_for,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import GroupSummary

ANONYMOUS = "anonymous"
"""Actor recorded for events without a logged-in admin (e.g. a failed login)."""


@dataclass(frozen=True)
class Authorization:
    """The outcome of ``AdminService.authorize``: decision, target and the group, if any."""

    decision: Decision
    target: Target
    group: GroupSummary | None = None


class AdminService:
    """Application service for the admin portal.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

    async def authorize(
        self,
        principal: Principal,
        permission: Permission,
        *,
        group_slug: str | None = None,
        organisation_slug: str | None = None,
    ) -> Authorization:
        """Decide whether ``principal`` may use ``permission``; audit every denial.

        Raises:
            DomainError: ``group_not_found`` for an unknown group.
            ValueError: If the target needed by the permission's level is missing.
        """
        level = LEVELS[permission]
        group: GroupSummary | None = None
        if level is Level.GROUP:
            if group_slug is None:
                raise ValueError(f"{permission} needs a group")
            async with self._uow.read() as tx:
                group = await tx.get_group_summary(group_slug)
            if group is None:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            target = Target.for_group(group.group.slug, group.organisation_slug)
        elif level is Level.ORGANISATION:
            if organisation_slug is None:
                raise ValueError(f"{permission} needs an organisation")
            target = Target.for_organisation(organisation_slug)
        else:
            target = Target.everything()
        decision = decide(principal, permission, target)
        if not decision.allowed:
            await self.record(
                principal.subject,
                permission.value,
                Outcome.DENIED,
                decision.reason.value,
                target.organisation,
                target.group,
            )
        return Authorization(decision, target, group)

    async def authorize_anywhere(
        self, principal: Principal, permission: Permission
    ) -> Authorization:
        """Decide whether ``principal`` may use ``permission`` on at least one target.

        For overview pages of organisation- or group-level permissions (for example the audit
        log of every organisation in scope); the page then lists only what the scope covers.
        Global permissions are delegated to ``authorize``. A denial is audited.
        """
        if LEVELS[permission] is Level.GLOBAL:
            return await self.authorize(principal, permission)
        if scope_for(principal, permission).is_empty:
            reason = Reason.NO_ROLE if not principal.roles else Reason.OUT_OF_SCOPE
            await self.record(principal.subject, permission.value, Outcome.DENIED, reason.value)
            return Authorization(Decision(False, reason), Target.everything())
        return Authorization(Decision(True, Reason.ROLE), Target.everything())

    async def record(
        self,
        actor: str,
        action: str,
        outcome: Outcome,
        reason: str = "",
        organisation: str | None = None,
        group: str | None = None,
    ) -> None:
        """Write an audit event (logins, denials, changes)."""
        event = AuditEvent(self._clock.now(), actor, action, outcome, reason, organisation, group)
        async with self._uow.locked() as tx:
            await tx.add_audit_event(event)

    async def groups(
        self, principal: Principal, permission: Permission = Permission.GROUP_VIEW
    ) -> list[GroupSummary]:
        """Return the groups ``principal`` may use ``permission`` on (database-side scope)."""
        async with self._uow.read() as tx:
            return await tx.list_groups(scope_for(principal, permission))

    async def audit_log(self, principal: Principal, limit: int = 100) -> list[AuditEvent]:
        """Return the newest audit events ``principal`` may see."""
        async with self._uow.read() as tx:
            return await tx.list_audit_events(scope_for(principal, Permission.AUDIT_VIEW), limit)
