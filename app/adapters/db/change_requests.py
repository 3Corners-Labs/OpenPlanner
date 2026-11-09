"""SQL queries of the machine API's change requests (ADR 0023 step 2).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. A request row
holds no personal data; the booking it matched, if any, is named by its id.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import ChangeRequestRow
from app.domain.change_requests import ChangeRequest


def _request(row: ChangeRequestRow) -> ChangeRequest:
    """Map a change request row."""
    return ChangeRequest(
        public_id=row.public_id,
        client_id=row.client_id,
        group_id=row.group_id,
        target_start=row.target_start,
        booking_id=row.booking_id,
        pinned_start=row.pinned_start,
        created_at=row.created_at,
        expires_at=row.expires_at,
        confirmed_at=row.confirmed_at,
        superseded_at=row.superseded_at,
        id=row.id,
    )


class ChangeRequestQueries:
    """The change requests' part of ``SqlTransaction``; needs ``_session``."""

    _session: AsyncSession

    async def add_change_request(self, request: ChangeRequest) -> ChangeRequest:
        """Insert a change request and return it with its id."""
        row = ChangeRequestRow(
            public_id=request.public_id,
            client_id=request.client_id,
            group_id=request.group_id,
            target_start=request.target_start,
            booking_id=request.booking_id,
            pinned_start=request.pinned_start,
            created_at=request.created_at,
            expires_at=request.expires_at,
            confirmed_at=request.confirmed_at,
            superseded_at=request.superseded_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _request(row)

    async def get_change_request(self, public_id: str) -> ChangeRequest | None:
        """Return the change request with ``public_id``."""
        row = await self._session.scalar(
            select(ChangeRequestRow).where(ChangeRequestRow.public_id == public_id)
        )
        return _request(row) if row is not None else None

    async def get_latest_change_request(self, booking_id: int) -> ChangeRequest | None:
        """Return the newest change request that matched the booking, open or not."""
        row = await self._session.scalar(
            select(ChangeRequestRow)
            .where(ChangeRequestRow.booking_id == booking_id)
            .order_by(ChangeRequestRow.id.desc())
            .limit(1)
        )
        return _request(row) if row is not None else None

    async def save_change_request(self, request: ChangeRequest) -> None:
        """Persist a request's confirmation or its replacement by a newer one."""
        await self._session.execute(
            update(ChangeRequestRow)
            .where(ChangeRequestRow.id == request.id)
            .values(confirmed_at=request.confirmed_at, superseded_at=request.superseded_at)
        )
