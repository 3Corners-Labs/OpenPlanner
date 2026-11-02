"""SQL queries of the admin session register (ADR 0098).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. Only the
``sha256`` of the session reference is stored; the claims are kept as JSON on the row.
"""

import json
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AdminSessionRow
from app.domain.admin_sessions import AdminSession, EndReason
from app.domain.authorization import Role
from app.domain.entitlements import LoginClaims


def claims_json(claims: LoginClaims) -> str:
    """Serialise the claims of a login (sets sorted, so equal claims give equal text)."""
    return json.dumps(
        {
            "name": claims.display_name,
            "roles": sorted(r.value for r in claims.roles),
            "organisations": sorted(claims.organisations),
            "groups": sorted(claims.groups),
            "role_claims": sorted(claims.role_claims),
            "entitlements": sorted(claims.entitlements),
        }
    )


def claims_from_json(subject: str, text: str) -> LoginClaims:
    """Rebuild the claims of a login from ``claims_json``."""
    data = json.loads(text)
    return LoginClaims(
        subject=subject,
        display_name=str(data["name"]),
        roles=frozenset(Role(r) for r in data["roles"]),
        organisations=frozenset(data["organisations"]),
        groups=frozenset(data["groups"]),
        role_claims=frozenset(data["role_claims"]),
        entitlements=frozenset(data["entitlements"]),
    )


def _session(row: AdminSessionRow) -> AdminSession:
    """Map a row to the domain object."""
    return AdminSession(
        id_hash=row.id_hash,
        subject=row.subject,
        provider=row.provider,
        claims=claims_from_json(row.subject, row.claims),
        login_at=row.login_at,
        last_seen_at=row.last_seen_at,
        ended_at=row.ended_at,
        end_reason=EndReason(row.end_reason) if row.end_reason else None,
    )


class AdminSessionQueries:
    """The admin sessions' part of ``SqlTransaction``; needs ``_session``."""

    _session: AsyncSession

    async def add_admin_session(self, session: AdminSession) -> None:
        """Store a new admin session."""
        self._session.add(
            AdminSessionRow(
                id_hash=session.id_hash,
                subject=session.subject,
                provider=session.provider,
                claims=claims_json(session.claims),
                login_at=session.login_at,
                last_seen_at=session.last_seen_at,
            )
        )
        await self._session.flush()

    async def get_admin_session(self, id_hash: str) -> AdminSession | None:
        """Return the session with this reference hash, ended or not."""
        row = await self._session.scalar(
            select(AdminSessionRow).where(AdminSessionRow.id_hash == id_hash)
        )
        return _session(row) if row else None

    async def active_admin_sessions_of(self, subject: str) -> list[AdminSession]:
        """Return the live sessions of ``subject``, oldest ``last_seen_at`` first."""
        rows = await self._session.scalars(
            select(AdminSessionRow)
            .where(AdminSessionRow.subject == subject, AdminSessionRow.ended_at.is_(None))
            .order_by(AdminSessionRow.last_seen_at, AdminSessionRow.id)
        )
        return [_session(row) for row in rows]

    async def end_admin_session(self, id_hash: str, reason: EndReason, at: datetime) -> bool:
        """End a live session; ``False`` when it is unknown or already ended."""
        result = await self._session.execute(
            update(AdminSessionRow)
            .where(AdminSessionRow.id_hash == id_hash, AdminSessionRow.ended_at.is_(None))
            .values(ended_at=at, end_reason=reason.value)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def touch_admin_session(self, id_hash: str, at: datetime) -> None:
        """Write the latest activity of a live session."""
        await self._session.execute(
            update(AdminSessionRow)
            .where(AdminSessionRow.id_hash == id_hash, AdminSessionRow.ended_at.is_(None))
            .values(last_seen_at=at)
        )

    async def end_all_admin_sessions(self, reason: EndReason, at: datetime) -> int:
        """End every live session with ``reason``; return how many."""
        result = await self._session.execute(
            update(AdminSessionRow)
            .where(AdminSessionRow.ended_at.is_(None))
            .values(ended_at=at, end_reason=reason.value)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def delete_admin_sessions(self, login_before: datetime) -> int:
        """Delete the sessions that ended or whose login lies before ``login_before``."""
        result = await self._session.execute(
            delete(AdminSessionRow).where(
                or_(AdminSessionRow.ended_at.is_not(None), AdminSessionRow.login_at < login_before)
            )
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
