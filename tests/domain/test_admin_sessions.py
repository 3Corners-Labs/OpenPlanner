"""The rules of the admin session register (ADR 0098): valid, idle, expired, limit."""

from datetime import timedelta

import pytest

from app.domain.admin_sessions import (
    AdminSession,
    EndReason,
    SessionPolicy,
    hash_sid,
    judge,
    new_sid,
    over_limit,
    seen_due,
)
from app.domain.entitlements import LoginClaims
from tests.support.domain import NOW

POLICY = SessionPolicy(
    lifetime=timedelta(hours=8),
    idle=timedelta(minutes=60),
    seen_interval=timedelta(minutes=5),
    max_sessions=3,
)


def _session(**changes: object) -> AdminSession:
    values: dict[str, object] = {
        "id_hash": "h",
        "subject": "sub",
        "provider": "platform",
        "claims": LoginClaims("sub", "Name"),
        "login_at": NOW,
        "last_seen_at": NOW,
    }
    return AdminSession(**{**values, **changes})  # type: ignore[arg-type]


def test_a_fresh_session_is_valid() -> None:
    assert judge(_session(), NOW + timedelta(minutes=1), POLICY) is None


def test_the_idle_limit_ends_a_session_with_reason_idle() -> None:
    later = NOW + timedelta(minutes=61)

    assert judge(_session(), later, POLICY) is EndReason.IDLE


def test_activity_keeps_a_session_within_the_idle_limit() -> None:
    session = _session(last_seen_at=NOW + timedelta(minutes=50))

    assert judge(session, NOW + timedelta(minutes=100), POLICY) is None


def test_the_absolute_lifetime_wins_over_activity() -> None:
    now = NOW + timedelta(hours=8, minutes=1)
    session = _session(last_seen_at=now)

    assert judge(session, now, POLICY) is EndReason.EXPIRED


def test_an_ended_session_stays_ended_with_its_own_reason() -> None:
    session = _session(ended_at=NOW, end_reason=EndReason.LOGOUT)

    assert judge(session, NOW, POLICY) is EndReason.LOGOUT


def test_last_seen_is_written_only_after_the_interval() -> None:
    assert not seen_due(_session(), NOW + timedelta(minutes=4), POLICY)
    assert seen_due(_session(), NOW + timedelta(minutes=5), POLICY)


def test_over_the_limit_the_oldest_last_seen_goes_first() -> None:
    active = [_session(id_hash=str(n), last_seen_at=NOW + timedelta(minutes=n)) for n in range(4)]

    ended = over_limit(active, 3)

    assert {s.id_hash for s in ended} == {"0", "1"}


def test_under_the_limit_nothing_goes() -> None:
    assert over_limit([_session(id_hash="a")], 3) == []


@pytest.mark.parametrize("limit", [1])
def test_a_limit_of_one_replaces_every_earlier_session(limit: int) -> None:
    assert len(over_limit([_session(id_hash="a")], limit)) == 1


def test_references_are_random_and_only_their_hash_is_compared() -> None:
    a, b = new_sid(), new_sid()

    assert a != b
    assert len(a) >= 43
    assert hash_sid(a) != a
    assert len(hash_sid(a)) == 64
