"""The screen name of a staff member, stored and erased in SQLite (ADR 0123 step 5c)."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Role
from app.domain.staff import Membership, StaffMember
from tests.support.domain import NOW

DEMO = Membership("org_admin", "demo")


def _person(subject: str = "sub-1") -> StaffMember:
    return StaffMember(
        subject, "Gina", frozenset({Role.ORG_ADMIN}), frozenset({DEMO}), NOW, NOW, NOW
    )


async def test_the_name_is_set_cleared_and_read_by_subject_and_by_id(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_person())
    async with uow.locked() as tx:
        assert await tx.set_staff_screen_name("sub-1", "Sanne") is True
        assert await tx.set_staff_screen_name("nobody", "Sanne") is False
    async with uow.read() as tx:
        by_subject = await tx.get_staff("sub-1")
        assert by_subject is not None
        assert by_subject.id is not None
        by_id = await tx.get_staff_by_id(by_subject.id)
        assert await tx.get_staff_by_id(999) is None

    assert by_subject.screen_name == "Sanne"
    assert by_id == by_subject
    assert "Sanne" not in repr(by_subject)  # personal data stays out of any repr
    async with uow.locked() as tx:
        await tx.set_staff_screen_name("sub-1", None)
    async with uow.read() as tx:
        cleared = await tx.get_staff("sub-1")
    assert cleared is not None
    assert cleared.screen_name is None


async def test_saving_the_record_at_a_login_keeps_what_the_member_carries(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(replace(_person(), screen_name="Sanne"))
    async with uow.read() as tx:
        member = await tx.get_staff("sub-1")
    assert member is not None
    assert member.screen_name == "Sanne"

    async with uow.locked() as tx:
        await tx.save_staff(replace(member, last_login_at=NOW + timedelta(days=1)))
    async with uow.read() as tx:
        again = await tx.get_staff("sub-1")

    assert again is not None
    assert again.screen_name == "Sanne"


async def test_erasing_clears_the_name_and_an_erased_record_takes_none(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(replace(_person(), screen_name="Sanne"))

    async with uow.locked() as tx:
        await tx.erase_staff("sub-1", NOW + timedelta(days=1))
    async with uow.locked() as tx:
        assert await tx.set_staff_screen_name("sub-1", "Again") is False
    async with uow.read() as tx:
        erased = await tx.get_staff("sub-1")

    assert erased is not None
    assert erased.screen_name is None
