"""Start, resolve and end admin sessions in the register (ADR 0098).

    start(claims, ...) ── end replaced ── end oldest over the limit ── add row ──► sid
    resolve(sid)       ── judge(): ended / expired / idle? ── end with reason ──► None
                       └─ valid ──► session (``last_seen_at`` written at most every interval)
    end(sid, reason)   ── logout and the like
    end_all(reason)    ── every live session, one audit event (CLI, restore)
    own(subject)       ── the person's live sessions, for "My sessions"
    end_others(...)    ── a person ends their other sessions (no permission needed)
    end_of_person(...) ── an administrator ends all sessions of a person in their scope
    clean(lifetime)    ── delete ended and past-lifetime rows (worker housekeeping)

The rules are in ``app.domain.admin_sessions``. When the register cannot be read the error
propagates: no session means no access (fail closed, ADR 0098 point 14).
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain.admin_sessions import (
    AUDITED_REASONS,
    AdminSession,
    EndReason,
    SessionPolicy,
    hash_sid,
    judge,
    new_sid,
    over_limit,
    seen_due,
)
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.entitlements import LoginClaims
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.staff import StaffFilter

SESSIONS_ENDED_ALL_EVENT = "admin.sessions_ended_all"
"""Audit action for ending every session at once; the detail holds only the count."""

SESSIONS_ENDED_BY_SELF_EVENT = "admin.sessions_ended_by_self"
"""Audit action for a person ending their other sessions; the detail holds only the count."""

SESSIONS_ENDED_BY_ADMIN_EVENT = "admin.sessions_ended_by_admin"
"""Audit action for an administrator ending all sessions of a person (ADR 0098 point 9b)."""

SESSION_ENDED_EVENT = "admin.session_ended"
"""Audit action for a session that ended without the person asking (idle, expired, limit)."""


@dataclass(frozen=True)
class PersonSessionsEnded:
    """The outcome of an administrator ending a person's sessions.

    Attributes:
        subject: Provider-qualified subject of the person, so the caller can tell whether the
            administrator ended their own sessions (the current one included).
        count: How many sessions ended.
    """

    subject: str
    count: int


class AdminSessionService:
    """Start, resolve and end admin sessions in the register."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Keep the unit of work and the clock."""
        self._uow = uow
        self._clock = clock

    async def start(
        self,
        claims: LoginClaims,
        provider: str,
        policy: SessionPolicy,
        replaces: str | None = None,
    ) -> str:
        """Create a session for ``claims`` and return its new reference.

        Args:
            claims: What the identity provider said about the person.
            provider: Key of that provider.
            policy: The session settings.
            replaces: The reference the browser brought along; its session is ended
                (``replaced``), so a copy of the old cookie is dead after this login.
        """
        now = self._clock.now()
        sid = new_sid()
        async with self._uow.locked() as tx:
            if replaces:
                await tx.end_admin_session(hash_sid(replaces), EndReason.REPLACED, now)
            active = await tx.active_admin_sessions_of(claims.subject)
            for old in over_limit(active, policy.max_sessions):
                await self._end(tx, old, EndReason.LIMIT, now)
            await tx.add_admin_session(
                AdminSession(hash_sid(sid), claims.subject, provider, claims, now, now)
            )
        return sid

    async def resolve(
        self, sid: str | None, policy: SessionPolicy
    ) -> tuple[AdminSession | None, bool]:
        """Return the live session behind ``sid`` and whether this request wrote activity.

        A session that has run out is ended with its reason and audited. When the register
        cannot be read the error propagates: no session means no access (fail closed).
        """
        if not sid:
            return None, False
        now = self._clock.now()
        id_hash = hash_sid(sid)
        async with self._uow.read() as tx:
            found = await tx.get_admin_session(id_hash)
        if found is None:
            return None, False
        reason = judge(found, now, policy)
        if reason is not None:
            if found.ended_at is None:
                async with self._uow.locked() as tx:
                    await self._end(tx, found, reason, now)
            return None, False
        if not seen_due(found, now, policy):
            return found, False
        async with self._uow.locked() as tx:
            await tx.touch_admin_session(id_hash, now)
        return replace(found, last_seen_at=now), True

    async def end(self, sid: str | None, reason: EndReason) -> None:
        """End the session behind ``sid`` (a no-op for an unknown or ended one)."""
        if not sid:
            return
        async with self._uow.locked() as tx:
            await tx.end_admin_session(hash_sid(sid), reason, self._clock.now())

    async def end_all(self, reason: EndReason, actor: str) -> int:
        """End every live session with ``reason`` and audit it once; return how many ended.

        One audit event whatever the count (also zero): actor, reason and the number, never an
        identifier or a subject (ADR 0098 point 9).
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            count = await tx.end_all_admin_sessions(reason, now)
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    actor,
                    SESSIONS_ENDED_ALL_EVENT,
                    Outcome.SUCCESS,
                    reason.value,
                    detail=f"sessions={count}",
                )
            )
        return count

    async def own(self, subject: str, policy: SessionPolicy) -> list[AdminSession]:
        """Return the live sessions of ``subject``, newest login first (ADR 0098 point 9a).

        Sessions that have run out but were not yet ended by a request are left out.
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            active = await tx.active_admin_sessions_of(subject)
        live = [s for s in active if judge(s, now, policy) is None]
        return sorted(live, key=lambda s: s.login_at, reverse=True)

    async def end_others(self, current: AdminSession) -> int:
        """End every other session of the person behind ``current``; return how many.

        Needs no permission: it only concerns the person's own sessions. The current one
        stays. One audit event with the person and the count, never an identifier.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            count = await tx.end_admin_sessions_of(
                current.subject, EndReason.ENDED_BY_SELF, now, keep=current.id_hash
            )
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    current.subject,
                    SESSIONS_ENDED_BY_SELF_EVENT,
                    Outcome.SUCCESS,
                    EndReason.ENDED_BY_SELF.value,
                    detail=f"sessions:{count}",
                )
            )
        return count

    async def end_of_person(
        self, principal: Principal, member_id: int
    ) -> PersonSessionsEnded | None:
        """End all sessions of the staff member ``member_id``; ``None`` if they are out of scope.

        The person must be one the administrator's scope for ``staff.sessions.end`` already
        shows in the staff overview (organisation level, the SQL adapter filters by it); for
        anyone else nothing is ended and the refusal is audited. A session belongs to the
        person, not to an organisation, so the sessions they use for other organisations end
        too (ADR 0098 point 9b). One audit event per organisation of the person inside the
        administrator's scope (so those administrators see it in their own log), with the
        person and the count.
        """
        now = self._clock.now()
        scope = scope_for(principal, Permission.STAFF_SESSIONS_END)
        async with self._uow.locked() as tx:
            found = await tx.list_staff(scope, StaffFilter(id=member_id))
            if not found:
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        principal.subject,
                        Permission.STAFF_SESSIONS_END.value,
                        Outcome.DENIED,
                        "out_of_scope",
                    )
                )
                return None
            member = found[0].within(scope)
            count = await tx.end_admin_sessions_of(member.subject, EndReason.ENDED_BY_ADMIN, now)
            detail = f"sessions:{count},subject:{member.subject}"[:255]
            places: list[str | None] = [*sorted(member.organisations)] or [None]
            for organisation in places:
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        principal.subject,
                        SESSIONS_ENDED_BY_ADMIN_EVENT,
                        Outcome.SUCCESS,
                        EndReason.ENDED_BY_ADMIN.value,
                        organisation,
                        detail=detail,
                    )
                )
        return PersonSessionsEnded(member.subject, count)

    async def clean(self, lifetime: timedelta) -> int:
        """Delete the ended rows and those past their absolute end; return how many went.

        History stays in ``audit_events``. A live session within ``lifetime`` is never touched.
        """
        async with self._uow.locked() as tx:
            return await tx.delete_admin_sessions(self._clock.now() - lifetime)

    @staticmethod
    async def _end(
        tx: Transaction, session: AdminSession, reason: EndReason, now: datetime
    ) -> None:
        """End ``session`` and audit it when the system chose the reason."""
        await tx.end_admin_session(session.id_hash, reason, now)
        if reason in AUDITED_REASONS:
            await tx.add_audit_event(
                AuditEvent(now, session.subject, SESSION_ENDED_EVENT, Outcome.SUCCESS, reason.value)
            )
