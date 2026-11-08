"""Own overview layouts in SQLite (ADR 0096 step 2): stored per staff member, keys in order."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Role
from app.domain.staff import Membership, StaffMember
from tests.support.domain import NOW


def _person(subject: str) -> StaffMember:
    demo = Membership("org_admin", "demo")
    return StaffMember(
        subject, "Gina", frozenset({Role.ORG_ADMIN}), frozenset({demo}), NOW, NOW, NOW
    )


async def test_a_layout_is_stored_replaced_and_deleted(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_person("sub-1"))
        assert await tx.get_overview_layout("sub-1") is None
        assert await tx.save_overview_layout("sub-1", ("queue", "alerts"), NOW)

    async with uow.locked() as tx:
        assert await tx.get_overview_layout("sub-1") == ("queue", "alerts")
        assert await tx.save_overview_layout("sub-1", ("rights",), NOW + timedelta(minutes=1))
    async with uow.read() as tx:
        assert await tx.get_overview_layout("sub-1") == ("rights",)

    async with uow.locked() as tx:
        assert await tx.save_overview_layout("sub-1", (), NOW)  # an empty overview is a choice
        assert await tx.get_overview_layout("sub-1") == ()
        assert await tx.save_overview_layout("sub-1", None, NOW)
        assert await tx.get_overview_layout("sub-1") is None


async def test_without_a_staff_record_nothing_is_stored(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        assert not await tx.save_overview_layout("nobody", ("queue",), NOW)
        assert await tx.get_overview_layout("nobody") is None


async def test_erasing_the_staff_member_deletes_the_layout(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_person("sub-2"))
        await tx.save_overview_layout("sub-2", ("queue",), NOW)
    async with uow.locked() as tx:
        await tx.erase_staff("sub-2", NOW)
    async with uow.read() as tx:
        assert await tx.get_overview_layout("sub-2") is None
