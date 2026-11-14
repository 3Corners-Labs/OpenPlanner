"""The first sighting of an installation webhook secret, as a fingerprint (ADR 0124 point 8)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.webhook_secrets import Sighting, fingerprint
from tests.support.domain import NOW

SIGHTING = Sighting(fingerprint(b"k" * 32, "the-secret"), NOW)


async def test_a_sighting_is_stored_per_platform_and_replaced(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        assert await tx.get_secret_sighting("wxcc") is None
    later = Sighting("a" * 64, NOW.replace(hour=12))

    async with uow.locked() as tx:
        await tx.save_secret_sighting("wxcc", SIGHTING)
        await tx.save_secret_sighting("webex", Sighting("f" * 64, NOW))
    async with uow.locked() as tx:
        await tx.save_secret_sighting("wxcc", later)

    async with uow.read() as tx:
        assert await tx.get_secret_sighting("wxcc") == later
        assert await tx.get_secret_sighting("webex") == Sighting("f" * 64, NOW)


async def test_the_row_holds_the_fingerprint_and_the_time_and_no_secret(
    engine: AsyncEngine,
) -> None:
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.save_secret_sighting("wxcc", SIGHTING)

    async with engine.connect() as connection:
        columns = (await connection.execute(text("SELECT * FROM webhook_secret_sightings"))).keys()
        rows = (await connection.execute(text("SELECT * FROM webhook_secret_sightings"))).all()
    assert list(columns) == ["platform", "fingerprint", "first_seen"]
    assert "the-secret" not in repr(rows)
