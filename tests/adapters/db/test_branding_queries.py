"""Branding storage (WP3b): name and colour on the organisation, logo in its own table."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.branding import Branding, Logo

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


async def test_branding_defaults_then_round_trips(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
    assert demo is not None
    assert demo.branding == Branding()

    async with uow.locked() as tx:
        await tx.save_branding(demo.id, Branding("Gemeente Demo", "#154273"))
    async with uow.read() as tx:
        by_id = await tx.get_organisation_by_id(demo.id)
        missing = await tx.get_organisation_by_id(999)
    assert by_id is not None
    assert by_id.branding == Branding("Gemeente Demo", "#154273")
    assert missing is None


async def test_logo_is_replaced_and_removed(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
        assert await tx.get_logo("demo") is None
    assert demo is not None

    first, second = Logo.from_upload(PNG), Logo.from_upload(PNG + b"\x02")
    for logo in (first, second):
        async with uow.locked() as tx:
            await tx.save_logo(demo.id, logo)
    async with uow.read() as tx:
        assert await tx.get_logo("demo") == second
        stored = await tx.get_organisation("demo")
        assert await tx.get_logo("andere") is None
    assert stored is not None
    assert stored.branding.logo == second.ref

    async with uow.locked() as tx:
        await tx.save_branding(demo.id, Branding("Nieuw"))  # keeps the logo
    async with uow.read() as tx:
        renamed = await tx.get_organisation("demo")
    assert renamed is not None
    assert renamed.branding.logo == second.ref

    async with uow.locked() as tx:
        await tx.save_logo(demo.id, None)
    async with uow.read() as tx:
        assert await tx.get_logo("demo") is None
        cleared = await tx.get_organisation("demo")
    assert cleared is not None
    assert cleared.branding.logo is None
