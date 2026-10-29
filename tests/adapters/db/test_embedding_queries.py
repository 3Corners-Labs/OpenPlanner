"""Storage of an organisation's allowed origins and a group's embedding switch (ADR 0035)."""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.embedding import AllowedOrigins

SITES = AllowedOrigins(
    ("https://www.gemeente.nl", "https://werk.gemeente.nl", "http://localhost:5173")
)


async def test_origins_round_trip_in_order_and_are_replaced_and_removed(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
        assert demo is not None
        assert await tx.get_allowed_origins(demo.id) == AllowedOrigins(("http://localhost:8001",))

    async with uow.locked() as tx:
        await tx.save_allowed_origins(demo.id, SITES)
    async with uow.read() as tx:
        assert await tx.get_allowed_origins(demo.id) == SITES

    fewer = AllowedOrigins(("https://werk.gemeente.nl",))
    async with uow.locked() as tx:
        await tx.save_allowed_origins(demo.id, fewer)
    async with uow.read() as tx:
        assert await tx.get_allowed_origins(demo.id) == fewer

    async with uow.locked() as tx:
        await tx.save_allowed_origins(demo.id, AllowedOrigins())
    async with uow.read() as tx:
        assert await tx.get_allowed_origins(demo.id) == AllowedOrigins()


async def test_the_embedding_switch_is_stored_with_the_group(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        group = await tx.get_group("andere-team")
    assert group is not None
    assert not group.embedding_enabled

    async with uow.locked() as tx:
        await tx.save_group(replace(group, embedding_enabled=True))
    async with uow.read() as tx:
        stored = await tx.get_group("andere-team")
    assert stored is not None
    assert stored.embedding_enabled
