"""SQL queries of the admin session register: only the hash is stored (ADR 0098)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import AdminSessionRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin_sessions import AdminSession, EndReason
from app.domain.authorization import Role
from app.domain.entitlements import LoginClaims
from tests.support.domain import NOW

CLAIMS = LoginClaims(
    "sub-1",
    "Sanne Super",
    roles=frozenset({Role.ORG_ADMIN}),
    organisations=frozenset({"demo"}),
    groups=frozenset({"team"}),
    role_claims=frozenset({"extra"}),
    entitlements=frozenset({"openplanner:x"}),
)


def _session(id_hash: str, minutes: int = 0, subject: str = "sub-1") -> AdminSession:
    at = NOW + timedelta(minutes=minutes)
    return AdminSession(id_hash, subject, "platform", CLAIMS, NOW, at)


@pytest.fixture
def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine, None)


async def test_a_session_round_trips_with_its_claims(uow: SqlUnitOfWork) -> None:
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("a" * 64))

    async with uow.read() as tx:
        found = await tx.get_admin_session("a" * 64)
        missing = await tx.get_admin_session("b" * 64)
    assert found is not None
    assert found.claims == CLAIMS
    assert (found.login_at, found.ended_at, found.end_reason) == (NOW, None, None)
    assert missing is None


async def test_ending_is_final_and_keeps_the_first_reason(uow: SqlUnitOfWork) -> None:
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("a" * 64))
        first = await tx.end_admin_session("a" * 64, EndReason.LOGOUT, NOW)
        second = await tx.end_admin_session("a" * 64, EndReason.IDLE, NOW)
    async with uow.read() as tx:
        found = await tx.get_admin_session("a" * 64)

    assert (first, second) == (True, False)
    assert found is not None
    assert found.end_reason is EndReason.LOGOUT


async def test_active_sessions_are_listed_oldest_seen_first_and_per_person(
    uow: SqlUnitOfWork,
) -> None:
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("2" * 64, 20))
        await tx.add_admin_session(_session("1" * 64, 10))
        await tx.add_admin_session(_session("3" * 64, 30))
        await tx.add_admin_session(_session("4" * 64, 0, subject="sub-2"))
        await tx.end_admin_session("3" * 64, EndReason.LOGOUT, NOW)
    async with uow.read() as tx:
        listed = await tx.active_admin_sessions_of("sub-1")

    assert [s.id_hash for s in listed] == ["1" * 64, "2" * 64]


async def test_touch_writes_activity_of_a_live_session_only(uow: SqlUnitOfWork) -> None:
    later = NOW + timedelta(minutes=9)
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("a" * 64))
        await tx.add_admin_session(_session("b" * 64))
        await tx.end_admin_session("b" * 64, EndReason.LOGOUT, NOW)
        await tx.touch_admin_session("a" * 64, later)
        await tx.touch_admin_session("b" * 64, later)
    async with uow.read() as tx:
        live, ended = await tx.get_admin_session("a" * 64), await tx.get_admin_session("b" * 64)

    assert live is not None
    assert live.last_seen_at == later
    assert ended is not None
    assert ended.last_seen_at == NOW


async def test_the_table_holds_hashes_never_a_reference(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("a" * 64))
    async with AsyncSession(engine) as session:
        columns = [c.name for c in AdminSessionRow.__table__.columns]
        row = (await session.scalars(select(AdminSessionRow))).one()

    assert "sid" not in columns
    assert row.id_hash == "a" * 64


async def test_end_all_ends_only_live_sessions_and_keeps_the_first_reason(
    uow: SqlUnitOfWork,
) -> None:
    async with uow.locked() as tx:
        await tx.add_admin_session(_session("1" * 64))
        await tx.add_admin_session(_session("2" * 64, subject="sub-2"))
        await tx.add_admin_session(_session("3" * 64))
        await tx.end_admin_session("3" * 64, EndReason.LOGOUT, NOW)

    async with uow.locked() as tx:
        ended = await tx.end_all_admin_sessions(EndReason.RESTORE, NOW + timedelta(minutes=5))

    async with uow.read() as tx:
        found = [await tx.get_admin_session(h * 64) for h in "123"]
    assert ended == 2
    assert [s.end_reason for s in found if s] == [
        EndReason.RESTORE,
        EndReason.RESTORE,
        EndReason.LOGOUT,
    ]


async def test_delete_removes_only_ended_or_past_lifetime_rows(uow: SqlUnitOfWork) -> None:
    fresh = AdminSession("1" * 64, "sub-1", "platform", CLAIMS, NOW, NOW)
    ended = AdminSession("2" * 64, "sub-1", "platform", CLAIMS, NOW, NOW)
    old = AdminSession("3" * 64, "sub-2", "platform", CLAIMS, NOW - timedelta(hours=9), NOW)
    async with uow.locked() as tx:
        for session in (fresh, ended, old):
            await tx.add_admin_session(session)
        await tx.end_admin_session("2" * 64, EndReason.LOGOUT, NOW)

    async with uow.locked() as tx:
        deleted = await tx.delete_admin_sessions(NOW - timedelta(hours=8))

    async with uow.read() as tx:
        left = [await tx.get_admin_session(h * 64) for h in "123"]
    assert deleted == 2
    assert [s is not None for s in left] == [True, False, False]


async def test_the_service_cleans_by_the_lifetime_it_is_given(uow: SqlUnitOfWork) -> None:
    from app.domain.admin_session_service import AdminSessionService
    from tests.fakes.clock import FixedClock

    old = AdminSession("1" * 64, "sub-1", "platform", CLAIMS, NOW - timedelta(hours=9), NOW)
    live = AdminSession("2" * 64, "sub-1", "platform", CLAIMS, NOW - timedelta(hours=7), NOW)
    async with uow.locked() as tx:
        await tx.add_admin_session(old)
        await tx.add_admin_session(live)

    deleted = await AdminSessionService(uow, FixedClock(NOW)).clean(timedelta(hours=8))

    async with uow.read() as tx:
        assert await tx.get_admin_session("1" * 64) is None
        assert await tx.get_admin_session("2" * 64) is not None
    assert deleted == 1
