"""The admin session as a record on the server (ADR 0098).

The browser holds only a random reference (``sid``); this module holds the rules for what the
record means. A session is usable while it is not ended, within its absolute lifetime and
within the idle limit. Starting a login ends the session the browser brought (``replaced``)
and keeps a person within ``max_sessions``: the one idle longest lapses (``limit``). The
identifier is hashed at once and never logged, audited or put in a URL.

The rules only; ``app.domain.admin_session_service`` applies them to the register.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.entitlements import LoginClaims

SID_BYTES = 32
"""Size of the random session reference (a fixed crypto parameter, ADR 0098 point 2)."""


class EndReason(StrEnum):
    """Why a session ended; stored as ``end_reason`` (ADR 0098 point 1)."""

    LOGOUT = "logout"
    IDLE = "idle"
    EXPIRED = "expired"
    REPLACED = "replaced"
    LIMIT = "limit"
    ENDED_BY_SELF = "ended_by_self"
    ENDED_BY_ADMIN = "ended_by_admin"
    RESTORE = "restore"
    SECRET_ROTATED = "secret_rotated"  # noqa: S105  # nosec B105 - a reason name, not a secret
    CLI = "cli"


AUDITED_REASONS = frozenset({EndReason.IDLE, EndReason.EXPIRED, EndReason.LIMIT})
"""Reasons the system chooses, so that nobody else audits them."""


@dataclass(frozen=True)
class SessionPolicy:
    """The settings that decide a session (class C, ADR 0098).

    Attributes:
        lifetime: Absolute lifetime from the login (``admin_session_hours``).
        idle: Idle limit (``admin_idle_minutes``).
        seen_interval: How often ``last_seen_at`` is written (``staff_last_seen_interval_seconds``).
        max_sessions: Sessions per person (``admin_max_sessions_per_person``).
    """

    lifetime: timedelta
    idle: timedelta
    seen_interval: timedelta
    max_sessions: int


@dataclass(frozen=True)
class AdminSession:
    """One row of the register.

    Attributes:
        id_hash: ``sha256`` of the reference, hex; the reference itself is never stored.
        subject: Provider-qualified subject of the person.
        provider: Key of the identity provider that logged the person in.
        claims: What the ID token said (kept here, not in the cookie).
        login_at: When the person logged in.
        last_seen_at: Last write of activity (at most every ``seen_interval``).
        ended_at: When it ended, or ``None`` while it lives.
        end_reason: Why it ended, or ``None``.
    """

    id_hash: str
    subject: str
    provider: str
    claims: LoginClaims
    login_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None = None
    end_reason: EndReason | None = None


def new_sid() -> str:
    """Return a fresh random session reference (URL-safe, 32 random bytes)."""
    return secrets.token_urlsafe(SID_BYTES)


def hash_sid(sid: str) -> str:
    """Return the ``sha256`` hex of a reference, the only form that is stored or compared."""
    return hashlib.sha256(sid.encode()).hexdigest()


def judge(session: AdminSession, now: datetime, policy: SessionPolicy) -> EndReason | None:
    """Return why ``session`` is no longer usable at ``now``, or ``None`` while it is.

    An ended session returns its own reason, so a replayed reference never revives it.
    """
    if session.ended_at is not None:
        return session.end_reason or EndReason.LOGOUT
    if now - session.login_at > policy.lifetime:
        return EndReason.EXPIRED
    if now - session.last_seen_at > policy.idle:
        return EndReason.IDLE
    return None


def seen_due(session: AdminSession, now: datetime, policy: SessionPolicy) -> bool:
    """Return whether ``last_seen_at`` is due to be written again."""
    return now - session.last_seen_at >= policy.seen_interval


def over_limit(active: list[AdminSession], max_sessions: int) -> list[AdminSession]:
    """Return the sessions to end so that one more login fits within ``max_sessions``.

    The ones idle longest go first.
    """
    keep = max(max_sessions - 1, 0)
    by_age = sorted(active, key=lambda s: s.last_seen_at, reverse=True)
    return by_age[keep:]
