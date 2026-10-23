from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.types import UTCDateTime

metadata = MetaData()
moments = Table(
    "moments", metadata, Column("id", Integer, primary_key=True), Column("at", UTCDateTime())
)


async def test_round_trip_returns_aware_utc(engine: AsyncEngine) -> None:
    amsterdam = timezone(timedelta(hours=2))
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(moments).values(id=1, at=datetime(2026, 6, 1, 11, 0, tzinfo=amsterdam))
        )
        value = (await conn.execute(select(moments.c.at))).scalar_one()

    assert value == datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    assert value.tzinfo is UTC


async def test_naive_datetimes_are_rejected(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        with pytest.raises(Exception, match="timezone-aware"):
            await conn.execute(insert(moments).values(id=2, at=datetime(2026, 6, 1, 9, 0)))  # noqa: DTZ001


async def test_null_passes_through(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(insert(moments).values(id=3, at=None))
        assert (await conn.execute(select(moments.c.at))).scalar_one() is None
