"""Suspending, reinstating and erasing staff records (ADR 0046 step 2, points 7, 9 and 10).

    suspend(principal, id)   ── rules ──► status suspended ── end the person's sessions ── audit
    reinstate(principal, id) ── rules ──► status active ── audit
    erase(principal, id)     ── superuser, suspended only ──► name, roles, memberships gone
                                                              ── end sessions ── audit
    erase_inactive(period)   ── worker, daily ──► everyone without a login for ``period``,
                                                  idempotent ── end sessions ── audit (system)

The identity provider stays the source of rights (ADR 0013); suspending only withholds them:
``LoginService.complete_login`` refuses a suspended person (reason ``suspended``), and the
sessions that were open end in the same transaction through the register's own function
(``end_admin_sessions_of``, ADR 0098), so there is no second way to end a session. An
administrator cannot suspend themselves, nor a superuser unless they are one. The status
change and the ending of the sessions are one transaction: a crash never leaves a suspended
person with a live session.

Audit events carry the person's subject and a count or reason code, never a name. One event
per organisation the person belongs to inside the actor's scope, so the administrators of that
organisation see it in their own log (ADR 0031); a person without a membership gets a global one.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.admin_sessions import EndReason
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal, Role, Scope, scope_for
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.staff import (
    ERASED_BY_RETENTION,
    ERASED_EVENT,
    ERASED_MANUALLY,
    REINSTATED_EVENT,
    SUSPENDED_EVENT,
    StaffFilter,
    StaffMember,
    StaffStatus,
)


class Result(StrEnum):
    """What a lifecycle request led to (the codes of the audit ``reason`` for a refusal)."""

    DONE = "done"
    UNCHANGED = "unchanged"
    OUT_OF_SCOPE = "out_of_scope"
    SELF = "self"
    SUPERUSER = "superuser"
    ERASED = "erased"
    NOT_SUSPENDED = "not_suspended"


@dataclass(frozen=True)
class LifecycleResult:
    """The outcome of one request.

    Attributes:
        outcome: What happened; anything but ``DONE`` and ``UNCHANGED`` changed nothing.
        sessions: How many sessions ended (suspend and erase).
    """

    outcome: Result
    sessions: int = 0

    @property
    def ok(self) -> bool:
        """Whether the person is now in the requested state."""
        return self.outcome in (Result.DONE, Result.UNCHANGED)


class StaffLifecycleService:
    """Suspend, reinstate and erase staff, and erase those who stayed away too long.

    Args:
        uow: Opens database transactions.
        clock: Current time.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

    async def suspend(self, principal: Principal, member_id: int) -> LifecycleResult:
        """Block the portal for a person and end their sessions (``staff.manage``)."""
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            member = await self._find(tx, principal, Permission.STAFF_MANAGE, scope, member_id, now)
            if member is None:
                return LifecycleResult(Result.OUT_OF_SCOPE)
            refusal = await self._refuse(tx, principal, scope, member, now)
            if refusal is not None:
                return refusal
            if member.status is StaffStatus.SUSPENDED:
                return LifecycleResult(Result.UNCHANGED)
            await tx.set_staff_status(member.subject, StaffStatus.SUSPENDED, now)
            ended = await tx.end_admin_sessions_of(member.subject, EndReason.SUSPENDED, now)
            await self._audit(tx, principal.subject, SUSPENDED_EVENT, member, scope, now, ended)
        return LifecycleResult(Result.DONE, ended)

    async def reinstate(self, principal: Principal, member_id: int) -> LifecycleResult:
        """Lift a suspension (``staff.manage``); the person can log in again."""
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            member = await self._find(tx, principal, Permission.STAFF_MANAGE, scope, member_id, now)
            if member is None:
                return LifecycleResult(Result.OUT_OF_SCOPE)
            refusal = await self._refuse(tx, principal, scope, member, now)
            if refusal is not None:
                return refusal
            if member.status is StaffStatus.ACTIVE:
                return LifecycleResult(Result.UNCHANGED)
            await tx.set_staff_status(member.subject, StaffStatus.ACTIVE, now)
            await self._audit(tx, principal.subject, REINSTATED_EVENT, member, scope, now)
        return LifecycleResult(Result.DONE)

    async def erase(self, principal: Principal, member_id: int) -> LifecycleResult:
        """Erase a suspended person's record (superuser, ``staff.erase``; the route checks it).

        The subject stays, so the audit log resolves to "an erased person"; the sessions end.
        """
        everything = Scope(everything=True)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            member = await self._find(
                tx, principal, Permission.STAFF_ERASE, everything, member_id, now
            )
            if member is None:
                return LifecycleResult(Result.OUT_OF_SCOPE)
            if member.subject == principal.subject:
                return await self._denied(tx, principal, member, Result.SELF, now)
            if member.status is StaffStatus.ERASED:
                return LifecycleResult(Result.UNCHANGED)
            if member.status is not StaffStatus.SUSPENDED:
                return LifecycleResult(Result.NOT_SUSPENDED)
            ended = await self._erase(tx, member, now)
            await self._audit(
                tx, principal.subject, ERASED_EVENT, member, everything, now, ended, ERASED_MANUALLY
            )
        return LifecycleResult(Result.DONE, ended)

    async def erase_inactive(self, retention: timedelta) -> int:
        """Erase everyone without a login for ``retention``; return how many (ADR 0046 point 9).

        Idempotent: an erased record is not erased again, so the daily worker run and a retry
        after a crash are the same thing. A person who logs in again before the cut-off is
        never touched, and one who logs in after it starts a fresh record.
        """
        now = self._clock.now()
        everything = Scope(everything=True)
        narrow = StaffFilter(dormant_before=now - retention)
        async with self._uow.read() as tx:
            due = [m for m in await tx.list_staff(everything, narrow) if _erasable(m)]
        for member in due:
            async with self._uow.locked() as tx:
                current = await tx.get_staff(member.subject)
                if (
                    current is None
                    or not _erasable(current)
                    or current.last_login_at >= (now - retention)
                ):
                    continue
                ended = await self._erase(tx, current, now)
                await self._audit(
                    tx, SYSTEM_ACTOR, ERASED_EVENT, current, everything, now, ended,
                    ERASED_BY_RETENTION, system=True,
                )  # fmt: skip
        return len(due)

    @staticmethod
    async def _find(
        tx: Transaction,
        principal: Principal,
        permission: Permission,
        scope: Scope,
        member_id: int,
        now: datetime,
    ) -> StaffMember | None:
        """Return the person in ``scope``, or audit the refusal and return ``None``."""
        found = await tx.list_staff(scope, StaffFilter(id=member_id))
        if found:
            return found[0]
        await tx.add_audit_event(
            AuditEvent(now, principal.subject, permission.value, Outcome.DENIED, "out_of_scope")
        )
        return None

    @staticmethod
    async def _refuse(
        tx: Transaction, principal: Principal, scope: Scope, member: StaffMember, now: datetime
    ) -> LifecycleResult | None:
        """Return the refusal for a request on ``member``, or ``None`` when it may go ahead.

        Nobody suspends themselves, and only a superuser touches a superuser (the rule that
        "an ``org_admin`` cannot suspend a superuser", ADR 0046 point 7). An erased record has
        nothing left to suspend or reinstate.
        """
        if member.subject == principal.subject:
            return await StaffLifecycleService._denied(tx, principal, member, Result.SELF, now)
        if Role.SUPERUSER in member.roles and not scope.everything:
            return await StaffLifecycleService._denied(tx, principal, member, Result.SUPERUSER, now)
        if member.status is StaffStatus.ERASED:
            return LifecycleResult(Result.ERASED)
        return None

    @staticmethod
    async def _denied(
        tx: Transaction,
        principal: Principal,
        member: StaffMember,
        outcome: Result,
        now: datetime,
    ) -> LifecycleResult:
        """Audit a refused request (reason code only) and return the refusal."""
        await tx.add_audit_event(
            AuditEvent(
                now,
                principal.subject,
                Permission.STAFF_MANAGE.value,
                Outcome.DENIED,
                outcome.value,
                detail=f"subject:{member.subject}"[:255],
            )
        )
        return LifecycleResult(outcome)

    @staticmethod
    async def _erase(tx: Transaction, member: StaffMember, now: datetime) -> int:
        """Erase the record and end the person's sessions in one transaction; return the count."""
        ended = await tx.end_admin_sessions_of(member.subject, EndReason.ERASED, now)
        await tx.erase_staff(member.subject, now)
        return ended

    @staticmethod
    async def _audit(
        tx: Transaction,
        actor: str,
        action: str,
        member: StaffMember,
        scope: Scope,
        now: datetime,
        sessions: int | None = None,
        reason: str = "",
        *,
        system: bool = False,
    ) -> None:
        """Write ``action`` once per organisation of ``member`` inside ``scope``."""
        parts = [f"subject:{member.subject}"]
        if sessions is not None:
            parts.insert(0, f"sessions:{sessions}")
        detail = ",".join(parts)[:255]
        places: list[str | None] = [*member.within(scope).organisations] or [None]
        for organisation in places:
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    actor,
                    action,
                    Outcome.SUCCESS,
                    reason,
                    organisation,
                    detail=detail,
                    source=Source.SYSTEM if system else Source.ADMIN,
                )
            )


def _erasable(member: StaffMember) -> bool:
    """Whether a record still holds something to erase."""
    return member.status is not StaffStatus.ERASED
