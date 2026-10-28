"""Admin use cases: authorise, audit and list, all through the matrix (ADR 0014).

Routes never decide on their own: they ask ``authorize`` and show what ``groups`` or
``audit_log`` return. Both the decision and the listing come from the same matrix, so they
cannot disagree.

    authorize(principal, permission, target)
        ├─ GROUP level: look up the group and its organisation (unknown → group_not_found)
        ├─ decide() ──► allowed  ──► return
        └─────────────► denied   ──► audit event (denied, reason) ──► return

Logins and logouts are recorded once per organisation and group the admin holds a role in,
so an organisation admin sees the logins of the admins of their own organisation; without
such a context (superuser, no role) the event is global and only the superuser sees it.
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.audit import AdminProfile, AuditEntry, AuditEvent, AuditFilter, Outcome
from app.domain.authorization import (
    LEVELS,
    Decision,
    Level,
    Permission,
    Principal,
    Reach,
    Reason,
    Target,
    decide,
    holds_a_role,
    reaches,
    role_keys,
    scope_for,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.retention import RetentionOverview, candidates_before, summarise
from app.domain.scheduling import GroupSummary

ANONYMOUS = "anonymous"
"""Actor recorded for events without a logged-in admin (e.g. a failed login)."""

LOGIN_EVENT = "admin.login"
LOGOUT_EVENT = "admin.logout"


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
            reason = Reason.OUT_OF_SCOPE if holds_a_role(principal) else Reason.NO_ROLE
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

    async def record_login(self, principal: Principal) -> None:
        """Audit a successful login and refresh the admin's profile, in one transaction."""
        async with self._uow.locked() as tx:
            await self._record_session_event(tx, principal, LOGIN_EVENT)
            await tx.save_admin_profile(
                AdminProfile(principal.subject, principal.display_name, self._clock.now())
            )

    async def record_logout(self, principal: Principal) -> None:
        """Audit a logout."""
        async with self._uow.locked() as tx:
            await self._record_session_event(tx, principal, LOGOUT_EVENT)

    async def _record_session_event(
        self, tx: Transaction, principal: Principal, action: str
    ) -> None:
        """Write ``action`` once per organisation/group context of ``principal``."""
        contexts: list[tuple[str | None, str | None]] = []
        reach = reaches(principal)
        if Reach.ORGANISATIONS in reach:
            contexts += [(organisation, None) for organisation in sorted(principal.organisations)]
        if Reach.GROUPS in reach:
            for slug in sorted(principal.groups):
                summary = await tx.get_group_summary(slug)
                if summary is not None:  # a claim for a group we do not know grants nothing
                    contexts.append((summary.organisation_slug, slug))
        roles = ",".join(role_keys(principal))
        now = self._clock.now()
        for organisation, group in contexts or [(None, None)]:
            event = AuditEvent(
                now, principal.subject, action, Outcome.SUCCESS, roles, organisation, group
            )
            await tx.add_audit_event(event)

    async def groups(
        self, principal: Principal, permission: Permission = Permission.GROUP_VIEW
    ) -> list[GroupSummary]:
        """Return the groups ``principal`` may use ``permission`` on (database-side scope)."""
        async with self._uow.read() as tx:
            return await tx.list_groups(scope_for(principal, permission))

    async def audit_log(
        self, principal: Principal, narrow: AuditFilter | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        """Return the newest audit events ``principal`` may see, with the actors' names.

        The scope is exact per level (ADR 0031): a group admin sees their groups' events, an
        organisation admin also the organisation's own, the superuser also global ones.
        ``narrow`` filters within that scope and never widens it.
        """
        scope = scope_for(principal, Permission.AUDIT_VIEW)
        async with self._uow.read() as tx:
            return await tx.list_audit_events(scope, limit, narrow)

    async def retention(self, principal: Principal) -> RetentionOverview:
        """Return whether personal data is removed on time, per group in scope (ADR 0039)."""
        now = self._clock.now()
        scope = scope_for(principal, Permission.RETENTION_VIEW)
        async with self._uow.read() as tx:
            facts = await tx.list_retention(scope, candidates_before(now))
        return summarise(facts, now)


def require(authorization: Authorization) -> Authorization:
    """Raise ``not_permitted`` unless the matrix allowed it (the denial is already audited).

    Use cases call this after ``AdminService.authorize``, so the route's check is never the
    only line of defence.
    """
    if not authorization.decision.allowed:
        raise DomainError(ErrorCode.NOT_PERMITTED)
    return authorization


async def record_change(
    tx: Transaction,
    at: datetime,
    principal: Principal,
    permission: Permission,
    organisation: str | None,
    group: str | None,
    detail: str = "",
) -> None:
    """Record a successful change in the same transaction as the change itself.

    ``detail`` names what changed (field names, ``rule.add``-style keys), never values.
    """
    event = AuditEvent(
        at, principal.subject, permission.value, Outcome.SUCCESS, "", organisation, group, detail
    )
    await tx.add_audit_event(event)
