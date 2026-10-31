"""Admin use cases: authorise, audit and list, all through the matrix (ADR 0014).

Routes never decide on their own: they ask ``authorize`` and show what ``groups`` or
``audit_log`` return. Both the decision and the listing come from the same matrix, so they
cannot disagree.

    authorize(principal, permission, target)
        ├─ GROUP level: look up the group and its organisation (unknown → group_not_found)
        ├─ decide() ──► allowed  ──► return
        └─────────────► denied   ──► audit event (denied, reason) ──► return

Logins and logouts are recorded once per organisation and group the admin holds a grant on
(their ``Membership``s), so an organisation admin sees the logins of the admins of their own
organisation; without such a context (superuser, no role) the event is global and only the
superuser sees it. Each login also refreshes the admin's staff record, with the entitlement
values that were refused (ADR 0049), and audits what changed in their roles and memberships
since the last one (ADR 0046).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.audit import AuditEntry, AuditEvent, AuditFilter, Outcome
from app.domain.audit_export import (
    EXPORT_EVENT,
    ExportLimits,
    ExportRefused,
    export_window,
    to_csv,
)
from app.domain.authorization import (
    LEVELS,
    Decision,
    Level,
    Permission,
    Principal,
    Reach,
    Reason,
    Scope,
    Target,
    decide,
    holds_a_role,
    reaches,
    role_keys,
    scope_for,
)
from app.domain.entitlements import Refusal
from app.domain.errors import DomainError, ErrorCode
from app.domain.event_schema import Envelope, to_record
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.retention import RetentionOverview, candidates_before, summarise
from app.domain.scheduling import GroupSummary
from app.domain.staff import (
    Membership,
    RefusedEntitlement,
    StaffFilter,
    StaffMember,
    change_events,
)
from app.domain.tasks import ActiveJobs

ANONYMOUS = "anonymous"
"""Actor recorded for events without a logged-in admin (e.g. a failed login)."""

LOGIN_EVENT = "admin.login"
LOGOUT_EVENT = "admin.logout"
ENTITLEMENT_REFUSED_EVENT = "auth.entitlement_refused"
"""ADR 0049 point 7: a login carried entitlement values this installation does not recognise."""
ENTITLEMENT_MISMATCH_EVENT = "auth.entitlement_mismatch"
"""ADR 0049 point 13: in shadow mode the entitlement claim disagrees with the legacy claims."""
AMBIGUOUS_CLAIMS_EVENT = "auth.ambiguous_claims"
"""ADR 0049 point 14: a login refused because its legacy claims were ambiguous."""


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
        if level is not Level.GLOBAL and group_slug is not None:
            async with self._uow.read() as tx:
                group = await tx.get_group_summary(group_slug)
            if group is None:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        if level is Level.GROUP:
            if group is None:
                raise ValueError(f"{permission} needs a group")
            target = Target.for_group(group.group.slug, group.organisation_slug)
        elif level is Level.ORGANISATION:
            # An organisation-level permission on a group's page: the group's organisation.
            if group is not None:
                organisation_slug = group.organisation_slug
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

    async def organisation_id(self, slug: str) -> int | None:
        """Return the id of the organisation ``slug``, or ``None`` when there is none.

        A request on an organisation shows that organisation's own words (ADR 0077).
        """
        async with self._uow.read() as tx:
            found = await tx.get_organisation(slug)
        return found.id if found is not None else None

    async def home_organisation(self, principal: Principal) -> str | None:
        """Return the one organisation ``principal`` belongs to, or ``None`` (ADR 0042 step 5).

        Its class D settings (time format, time zone) then apply on the admin's pages. The
        superuser, and an admin of more than one organisation, follow the platform values.
        """
        if Reach.EVERYTHING in reaches(principal):
            return None
        slugs = {g.organisation for g in principal.grants if g.organisation is not None}
        return next(iter(slugs)) if len(slugs) == 1 else None

    async def record(
        self,
        actor: str,
        action: str,
        outcome: Outcome,
        reason: str = "",
        organisation: str | None = None,
        group: str | None = None,
        detail: str = "",
    ) -> None:
        """Write an audit event (logins, denials, changes)."""
        event = AuditEvent(
            self._clock.now(), actor, action, outcome, reason, organisation, group, detail
        )
        async with self._uow.locked() as tx:
            await tx.add_audit_event(event)

    async def record_login(
        self,
        principal: Principal,
        refused: Sequence[Refusal] = (),
        mismatch: str | None = None,
    ) -> None:
        """Audit a login and refresh the admin's staff record, in one transaction (ADR 0046).

        ``refused`` entitlement values are kept on the record and audited once per
        organisation they name (global for values naming none), with the values and reason
        codes as detail (ADR 0049 point 7). A shadow-mode ``mismatch`` is audited as well.
        """
        now = self._clock.now()
        memberships = memberships_of(principal)
        async with self._uow.locked() as tx:
            await self._record_session_event(tx, principal, LOGIN_EVENT, memberships)
            old = await tx.get_staff(principal.subject)
            new = StaffMember(
                subject=principal.subject,
                display_name=principal.display_name,
                roles=principal.roles,
                memberships=frozenset(memberships),
                first_seen_at=old.first_seen_at if old else now,
                last_login_at=now,
                last_seen_at=now,
                last_logout_at=old.last_logout_at if old else None,
                refused=tuple(
                    RefusedEntitlement(r.value, r.reason.value, r.organisation, now)
                    for r in refused
                ),
            )
            for event in change_events(old, new):
                await tx.add_audit_event(event)
            for event in refusal_events(principal.subject, now, refused):
                await tx.add_audit_event(event)
            if mismatch is not None:
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        principal.subject,
                        ENTITLEMENT_MISMATCH_EVENT,
                        Outcome.FAILURE,
                        "",
                        None,
                        None,
                        mismatch,
                    )
                )
            await tx.save_staff(new)

    async def record_refused_login(self, subject: str, reason: str) -> None:
        """Audit a refused login (``ambiguous_claims`` or ``too_many_entitlements``)."""
        await self.record(subject, AMBIGUOUS_CLAIMS_EVENT, Outcome.DENIED, reason)

    async def record_logout(self, principal: Principal) -> None:
        """Audit a logout and note it on the admin's staff record."""
        memberships = memberships_of(principal)
        async with self._uow.locked() as tx:
            await self._record_session_event(tx, principal, LOGOUT_EVENT, memberships)
            await tx.set_staff_logout(principal.subject, self._clock.now())

    async def record_seen(self, principal: Principal) -> None:
        """Note that the admin is active now (the caller throttles how often)."""
        async with self._uow.locked() as tx:
            await tx.set_staff_seen(principal.subject, self._clock.now())

    async def _record_session_event(
        self, tx: Transaction, principal: Principal, action: str, memberships: list[Membership]
    ) -> None:
        """Write ``action`` once per membership of ``principal`` (global without any)."""
        roles = ",".join(role_keys(principal))
        now = self._clock.now()
        contexts: list[tuple[str | None, str | None]] = [
            (m.organisation, m.group) for m in memberships
        ]
        for organisation, group in contexts or [(None, None)]:
            event = AuditEvent(
                now, principal.subject, action, Outcome.SUCCESS, roles, organisation, group
            )
            await tx.add_audit_event(event)

    async def staff(
        self, principal: Principal, narrow: StaffFilter | None = None
    ) -> list[StaffMember]:
        """Return the staff ``principal`` may see, each with only the memberships in scope."""
        scope = scope_for(principal, Permission.STAFF_VIEW)
        async with self._uow.read() as tx:
            members = await tx.list_staff(scope, narrow or StaffFilter())
        return [m.within(scope) for m in members]

    async def staff_member(self, principal: Principal, member_id: int) -> StaffMember | None:
        """Return one person if ``principal`` may see them, else ``None`` (as if unknown)."""
        found = await self.staff(principal, StaffFilter(id=member_id))
        return found[0] if found else None

    async def own_refusals(self, principal: Principal) -> tuple[RefusedEntitlement, ...]:
        """Return the entitlement values of the admin's own last login that were refused.

        For the *My rights* page (ADR 0049 point 8): one's own record, no scope involved.
        """
        async with self._uow.read() as tx:
            member = await tx.get_staff(principal.subject)
        return member.refused if member else ()

    async def groups(
        self, principal: Principal, permission: Permission = Permission.GROUP_VIEW
    ) -> list[GroupSummary]:
        """Return the groups ``principal`` may use ``permission`` on (database-side scope)."""
        async with self._uow.read() as tx:
            return await tx.list_groups(scope_for(principal, permission))

    async def organisation_name(self, slug: str) -> str:
        """Return the name of organisation ``slug`` for a page header, or the slug if unknown.

        The route that calls this has already authorised the organisation (ADR 0014); the
        name is what the admin sees in the header and breadcrumbs (ADR 0047 step 3).
        """
        async with self._uow.read() as tx:
            found = await tx.get_organisation(slug)
        return found.name if found else slug

    async def active_jobs(self, limit: int) -> ActiveJobs:
        """Return the running and failed background jobs for the overview (ADR 0061 §2).

        Platform-wide: the widget's route carries ``dashboard.view``, a global permission,
        and a job is shown by type, state and moment only, never by its booking.
        """
        async with self._uow.read() as tx:
            return await tx.active_jobs(limit)

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

    async def export_audit(
        self,
        principal: Principal,
        narrow: AuditFilter,
        envelope: Envelope,
        limits: ExportLimits,
    ) -> tuple[str, int]:
        """Return the CSV of exactly ``narrow`` inside the principal's scope, and its row count.

        Without a period the last ``limits.max_days`` are exported. Every export, and every
        refused one, is itself an audit event (``audit.export``, detail ``rows:<n>``) at the
        level of what was exported (ADR 0040, part 3).

        Raises:
            ExportRefused: ``too_many_days`` or ``too_many_rows``; narrow the filter.
        """
        scope = scope_for(principal, Permission.AUDIT_EXPORT)
        try:
            since, until = export_window(narrow.since, narrow.until, self._clock.now(), limits)
            async with self._uow.read() as tx:
                entries = await tx.list_audit_events(
                    scope, limits.max_rows + 1, replace(narrow, since=since, until=until)
                )
            if len(entries) > limits.max_rows:
                raise ExportRefused("too_many_rows")
        except ExportRefused as refused:
            await self._record_export(principal, scope, narrow, Outcome.FAILURE, refused.code)
            raise
        await self._record_export(principal, scope, narrow, Outcome.SUCCESS, "", len(entries))
        return to_csv(to_record(entry.event, envelope) for entry in entries), len(entries)

    async def _record_export(
        self,
        principal: Principal,
        scope: Scope,
        narrow: AuditFilter,
        outcome: Outcome,
        reason: str,
        rows: int = 0,
    ) -> None:
        """Audit an export at the narrowest level that covers it."""
        async with self._uow.locked() as tx:
            summary = await tx.get_group_summary(narrow.group) if narrow.group else None
            if summary is not None:
                organisation, group = summary.organisation_slug, summary.group.slug
            elif not scope.everything and len(scope.organisations) == 1 and not scope.groups:
                organisation, group = next(iter(scope.organisations)), None
            else:
                organisation, group = None, None
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    principal.subject,
                    EXPORT_EVENT,
                    outcome,
                    reason,
                    organisation,
                    group,
                    f"rows:{rows}" if outcome is Outcome.SUCCESS else "",
                )
            )

    async def retention(self, principal: Principal) -> RetentionOverview:
        """Return whether personal data is removed on time, per group in scope (ADR 0039)."""
        now = self._clock.now()
        scope = scope_for(principal, Permission.RETENTION_VIEW)
        async with self._uow.read() as tx:
            facts = await tx.list_retention(scope, candidates_before(now))
        return summarise(facts, now)


def memberships_of(principal: Principal) -> list[Membership]:
    """Return one membership per grant on an organisation or group, in a stable order.

    The superuser's grant names no target and is no membership; its role is on the record.
    """
    found = [
        Membership(g.role, g.organisation, g.group)
        for g in principal.grants
        if g.organisation is not None
    ]
    return sorted(found, key=lambda m: m.sort_key)


def refusal_events(subject: str, now: datetime, refused: Sequence[Refusal]) -> list[AuditEvent]:
    """Return one ``auth.entitlement_refused`` event per organisation named (ADR 0049 point 7).

    Values naming no known organisation go into one global event, which only the superuser
    sees. The detail lists ``value=reason`` pairs; values hold prefixes, role keys and IdP
    values, never personal data. A detail that would not fit is counted instead.
    """
    by_organisation: dict[str | None, list[Refusal]] = {}
    for refusal in refused:
        by_organisation.setdefault(refusal.organisation, []).append(refusal)
    events = []
    for organisation, found in sorted(by_organisation.items(), key=lambda item: item[0] or ""):
        detail = ",".join(f"{r.value}={r.reason.value}" for r in found)
        if len(detail) > 255:
            detail = f"refused:{len(found)}"
        events.append(
            AuditEvent(
                now,
                subject,
                ENTITLEMENT_REFUSED_EVENT,
                Outcome.FAILURE,
                "",
                organisation,
                None,
                detail,
            )
        )
    return events


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
