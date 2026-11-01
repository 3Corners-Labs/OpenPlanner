"""Start, resolve and end admin sessions in the register (ADR 0098).

    start(claims, ...) ── end replaced ── end oldest over the limit ── add row ──► sid
    resolve(sid)       ── judge(): ended / expired / idle? ── end with reason ──► None
                       └─ valid ──► session (``last_seen_at`` written at most every interval)
    end(sid, reason)   ── logout and the like

The rules are in ``app.domain.admin_sessions``. When the register cannot be read the error
propagates: no session means no access (fail closed, ADR 0098 point 14).
"""

from dataclasses import replace
from datetime import datetime

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
from app.domain.entitlements import LoginClaims
from app.domain.ports import Clock, Transaction, UnitOfWork

SESSION_ENDED_EVENT = "admin.session_ended"
"""Audit action for a session that ended without the person asking (idle, expired, limit)."""


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
