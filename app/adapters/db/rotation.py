"""Reads for the key-rotation counters of System status (ADR 0094).

Two read-only queries over the engine: how many sealed values are not under the current
settings key, and the salt and token hash of every open booking. Raw SQL is allowed in this
package only (CLAUDE.md). Nothing is decrypted and no value is returned beyond the hash
and salt the domain needs to count; the caller reduces them to numbers.
"""

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.crypto import SecretBox
from app.adapters.db.models import BookingRow, SlotRow
from app.adapters.db.sealed import SEALED
from app.domain.booking import HOLDS_PLACE
from app.domain.key_rotation import BookingCredential

_HOLDS_PLACE = sorted(status.value for status in HOLDS_PLACE)


async def records_not_current(engine: AsyncEngine, box: SecretBox) -> int:
    """Return how many sealed values are not under ``box``'s current key (ADR 0094 point 3)."""
    stale = 0
    async with engine.connect() as connection:
        for *_, query in SEALED:
            result = await connection.exec_driver_sql(query)
            rows = cast("list[tuple[int, str, bytes, bytes]]", result.all())
            stale += sum(1 for _rowid, _name, nonce, _cipher in rows if not box.is_current(nonce))
    return stale


async def open_booking_credentials(engine: AsyncEngine, now: datetime) -> list[BookingCredential]:
    """Return salt and token hash of every booking that still holds a place and has not ended.

    Those are the bookings that can still be sent a message with their link (ADR 0094 point 4).
    """
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(BookingRow.management_token_salt, BookingRow.management_token_hash)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(BookingRow.status.in_(_HOLDS_PLACE), SlotRow.end > now)
        )
        return [BookingCredential(salt, token_hash) for salt, token_hash in rows]
