"""Storage of a group's latest integration check (verify button)."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.checks import CheckArea, CheckStatus, Finding, GroupCheck
from tests.support.domain import NOW

FAIL = Finding(CheckArea.WXCC, "wxcc.channel.missing", CheckStatus.FAIL, (("channel", "x"),))
OK = Finding(CheckArea.WEBEX, "webex.host_ok", CheckStatus.OK)


async def test_latest_check_round_trips_and_is_replaced(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        demo = await tx.get_group("demo")
        assert demo is not None
        assert await tx.get_group_check(demo.id) is None

    async with uow.locked() as tx:
        await tx.save_group_check(GroupCheck(demo.id, NOW, (FAIL, OK)))
    async with uow.read() as tx:
        first = await tx.get_group_check(demo.id)
    assert first == GroupCheck(demo.id, NOW, (FAIL, OK))

    later = NOW + timedelta(minutes=5)
    async with uow.locked() as tx:
        await tx.save_group_check(GroupCheck(demo.id, later, (OK,)))
    async with uow.read() as tx:
        second = await tx.get_group_check(demo.id)
    assert second == GroupCheck(demo.id, later, (OK,))
