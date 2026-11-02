"""SQL queries of the participant link sessions (ADR 0093).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. Only the
``sha256`` of the cookie value is stored, and bookings are named by id.
"""

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    BookingRow,
    ParticipantLinkBookingRow,
    ParticipantLinkSessionRow,
)
from app.domain.participant_links import LinkSession


class ParticipantLinkQueries:
    """The participant link sessions' part of ``SqlTransaction``; needs ``_session``."""

    _session: AsyncSession

    async def get_booking_id_by_public_ref(self, public_ref: str) -> int | None:
        """Return the id of the booking named by ``public_ref``, or ``None``."""
        return await self._session.scalar(
            select(BookingRow.id).where(BookingRow.public_ref == public_ref)
        )

    async def add_link_session(self, value_hash: str, now: datetime, expires_at: datetime) -> int:
        """Store a new session and return its id."""
        row = ParticipantLinkSessionRow(
            value_hash=value_hash, created_at=now, last_used_at=now, expires_at=expires_at
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def get_link_session(self, value_hash: str) -> LinkSession | None:
        """Return the session with this value hash and its bookings, oldest first."""
        row = await self._session.scalar(
            select(ParticipantLinkSessionRow).where(
                ParticipantLinkSessionRow.value_hash == value_hash
            )
        )
        if row is None:
            return None
        ids = await self._session.scalars(
            select(ParticipantLinkBookingRow.booking_id)
            .where(ParticipantLinkBookingRow.session_id == row.id)
            .order_by(ParticipantLinkBookingRow.added_at, ParticipantLinkBookingRow.id)
        )
        return LinkSession(row.id, row.value_hash, tuple(ids), row.last_used_at, row.expires_at)

    async def touch_link_session(
        self, session_id: int, now: datetime, expires_at: datetime
    ) -> None:
        """Record a use of the session and move its end."""
        await self._session.execute(
            update(ParticipantLinkSessionRow)
            .where(ParticipantLinkSessionRow.id == session_id)
            .values(last_used_at=now, expires_at=expires_at)
        )

    async def add_booking_to_link_session(
        self, session_id: int, booking_id: int, now: datetime
    ) -> None:
        """Let the session hold the booking; a booking it already holds is left as it is."""
        held = await self._session.scalar(
            select(ParticipantLinkBookingRow.id).where(
                ParticipantLinkBookingRow.session_id == session_id,
                ParticipantLinkBookingRow.booking_id == booking_id,
            )
        )
        if held is None:
            self._session.add(
                ParticipantLinkBookingRow(
                    session_id=session_id, booking_id=booking_id, added_at=now
                )
            )
            await self._session.flush()

    async def remove_booking_from_link_session(self, session_id: int, booking_id: int) -> None:
        """Take the booking out of the session."""
        await self._session.execute(
            delete(ParticipantLinkBookingRow).where(
                ParticipantLinkBookingRow.session_id == session_id,
                ParticipantLinkBookingRow.booking_id == booking_id,
            )
        )

    async def delete_link_session(self, session_id: int) -> None:
        """Delete one session and its list of bookings."""
        await self._session.execute(
            delete(ParticipantLinkBookingRow).where(
                ParticipantLinkBookingRow.session_id == session_id
            )
        )
        await self._session.execute(
            delete(ParticipantLinkSessionRow).where(ParticipantLinkSessionRow.id == session_id)
        )

    async def delete_lapsed_link_sessions(self, now: datetime) -> int:
        """Delete the sessions that lapsed before ``now`` with their bookings; count them."""
        lapsed = select(ParticipantLinkSessionRow.id).where(
            ParticipantLinkSessionRow.expires_at <= now
        )
        await self._session.execute(
            delete(ParticipantLinkBookingRow).where(
                ParticipantLinkBookingRow.session_id.in_(lapsed)
            )
        )
        result = await self._session.execute(
            delete(ParticipantLinkSessionRow).where(ParticipantLinkSessionRow.expires_at <= now)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
