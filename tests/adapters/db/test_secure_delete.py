"""Purged personal data does not stay readable in the database file or its WAL (WP-A3, PI-08)."""

from dataclasses import replace
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.maintenance import checkpoint_wal
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY, book

MARKER = "zzmarker-purge-check"
"""Appears in the first name, the last name and the email of the booking under test."""


def _bytes(database_url: str) -> tuple[bytes, bytes]:
    """Return the bytes of the database file and of its ``-wal`` file (empty when absent)."""
    file = Path(database_url.removeprefix("sqlite+aiosqlite:///"))
    wal = file.with_name(f"{file.name}-wal")
    return file.read_bytes(), wal.read_bytes() if wal.exists() else b""


async def _purged_booking(engine: AsyncEngine) -> None:
    """Book with the marker in every personal field, then purge the booking."""
    await seed_group(engine)
    uow = SqlUnitOfWork(engine)
    contact = replace(
        ADA, first_name=MARKER, last_name=f"{MARKER}-b", email=f"{MARKER}@example.org"
    )
    receipt = await book(
        BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY), SLOT_0900, "aangifte", contact
    )
    async with uow.locked() as tx:
        await tx.purge_personal_data(receipt.booking.id or 0, NOW)


async def test_every_connection_overwrites_deleted_content(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA secure_delete"))).scalar_one() == 1


async def test_a_purged_value_is_gone_from_the_file_and_the_wal(
    engine: AsyncEngine, database_url: str
) -> None:
    await _purged_booking(engine)

    await checkpoint_wal(engine)

    database, wal = _bytes(database_url)
    assert MARKER.encode() not in database
    assert MARKER.encode() not in wal
    assert wal == b""  # TRUNCATE leaves an empty log


async def test_without_the_checkpoint_the_wal_still_holds_the_value(
    engine: AsyncEngine, database_url: str
) -> None:
    """Guards the test above: the marker really is in the WAL before the checkpoint."""
    await _purged_booking(engine)

    database, wal = _bytes(database_url)

    assert MARKER.encode() in database + wal
