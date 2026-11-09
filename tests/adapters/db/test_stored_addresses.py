"""The stored address of a staff member comes first, the directory is the fallback (ADR 0117)."""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.staff_addresses import StoredAddresses
from app.domain.staff import StaffMember
from tests.fakes.digest import FakeStaffAddresses
from tests.support.domain import NOW


def _staff(subject: str, **values: object) -> StaffMember:
    return replace(StaffMember(subject, "Olga", frozenset(), frozenset(), NOW, NOW, NOW), **values)


async def test_the_stored_address_comes_first_for_any_provider(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_staff("idp:org-a:u1", email="olga@voorbeeld.nl", email_at=NOW))
    fallback = FakeStaffAddresses({"idp:org-a:u1": "oud@voorbeeld.nl"})

    found = await StoredAddresses(uow, fallback).address_of("idp:org-a:u1")

    assert found == "olga@voorbeeld.nl"
    assert fallback.asked == []


async def test_a_person_without_a_stored_address_falls_back_to_the_directory(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_staff("u1"))
    fallback = FakeStaffAddresses({"u1": "dir@voorbeeld.nl", "unknown": "dir@voorbeeld.nl"})

    assert await StoredAddresses(uow, fallback).address_of("u1") == "dir@voorbeeld.nl"
    assert await StoredAddresses(uow, fallback).address_of("unknown") == "dir@voorbeeld.nl"


async def test_an_erased_person_has_no_address_and_the_directory_is_not_asked(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_staff("u1", email="olga@voorbeeld.nl", email_at=NOW))
        await tx.erase_staff("u1", NOW)
    fallback = FakeStaffAddresses({"u1": "dir@voorbeeld.nl", "unknown": "dir@voorbeeld.nl"})

    assert await StoredAddresses(uow, fallback).address_of("u1") is None
    assert fallback.asked == []
