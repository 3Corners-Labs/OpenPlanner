"""Storage of an organisation's default contact block (ADR 0037)."""

import json

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import OrganisationContactsRow
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.contacts import ContactBlock, ContactKind, ContactOption

PHONE = ContactOption.create(
    ContactKind.PHONE, "14 020", labels={"nl": "Bel"}, hours={"nl": "ma-vr", "en": "Mon-Fri"}
)
ADDRESS = ContactOption.create(ContactKind.ADDRESS, "Stadhuis\nAmstel 1")
BLOCK = ContactBlock((PHONE, ADDRESS), {"nl": "Andere manieren", "en": "Other ways"})


async def _demo_id(uow: SqlUnitOfWork) -> int:
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
    assert demo is not None
    return demo.id


async def test_block_round_trips_is_replaced_and_removed(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    demo = await _demo_id(uow)
    async with uow.read() as tx:
        assert await tx.get_contact_block(demo) is None

    async with uow.locked() as tx:
        await tx.save_contact_block(demo, BLOCK)
    async with uow.read() as tx:
        assert await tx.get_contact_block(demo) == BLOCK

    smaller = ContactBlock((ADDRESS,))
    async with uow.locked() as tx:
        await tx.save_contact_block(demo, smaller)
    async with uow.read() as tx:
        assert await tx.get_contact_block(demo) == smaller

    async with uow.locked() as tx:
        await tx.save_contact_block(demo, None)
    async with uow.read() as tx:
        assert await tx.get_contact_block(demo) is None


async def test_options_another_version_wrote_are_left_out(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    demo = await _demo_id(uow)
    async with uow.locked() as tx:
        await tx.save_contact_block(demo, BLOCK)
    body = {
        "headings": {"nl": "Kop"},
        "options": [
            {"kind": "fax", "value": "020"},
            {"kind": "website", "value": "javascript:alert(1)"},
            {"value": "no kind"},
            {"kind": "phone", "value": "14 020"},
        ],
    }
    async with engine.begin() as connection:
        await connection.execute(
            update(OrganisationContactsRow)
            .where(OrganisationContactsRow.organisation_id == demo)
            .values(body=json.dumps(body))
        )

    async with uow.read() as tx:
        block = await tx.get_contact_block(demo)
    assert block == ContactBlock((ContactOption(ContactKind.PHONE, "14 020"),), {"nl": "Kop"})


async def test_a_groups_own_block_round_trips_and_is_removed(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        group = await tx.get_group("demo")
    assert group is not None

    async with uow.locked() as tx:
        await tx.save_group_contact_block(group.id, BLOCK)
    async with uow.read() as tx:
        assert await tx.get_group_contact_block(group.id) == BLOCK
        assert await tx.get_contact_block(group.organisation_id) is None  # apart from the default

    async with uow.locked() as tx:
        await tx.save_group_contact_block(group.id, None)
    async with uow.read() as tx:
        assert await tx.get_group_contact_block(group.id) is None
