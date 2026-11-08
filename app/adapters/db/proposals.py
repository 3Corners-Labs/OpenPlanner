"""SQL queries of held proposals of a new time (ADR 0057), and the places a slot has taken.

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. A proposal is
*open* while it is not released and its booking still awaits a new time; an open proposal takes
a place in its slot like a booking that holds one (``places_taken``), so every capacity check of
a booking, a move and the availability sees it.
"""

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.adapters.db.models import BookingRow, RescheduleProposalRow, SlotRow
from app.domain.booking import HOLDS_PLACE, BookingStatus
from app.domain.reschedule_proposal import HeldProposal, ProposalOutcome

_HOLDS_PLACE = sorted(status.value for status in HOLDS_PLACE)
_AWAITING = BookingStatus.AWAITING_REBOOK.value


def places_taken() -> ColumnElement[int]:
    """Return the places taken in the outer query's ``SlotRow``: bookings and open proposals."""
    booking = aliased(BookingRow)
    booked = (
        select(func.count(booking.id))
        .where(booking.slot_id == SlotRow.id, booking.status.in_(_HOLDS_PLACE))
        .correlate(SlotRow)
        .scalar_subquery()
    )
    owner = aliased(BookingRow)
    held = (
        select(func.count(RescheduleProposalRow.id))
        .join(owner, owner.id == RescheduleProposalRow.booking_id)
        .where(
            RescheduleProposalRow.slot_id == SlotRow.id,
            RescheduleProposalRow.released_at.is_(None),
            owner.status == _AWAITING,
        )
        .correlate(SlotRow)
        .scalar_subquery()
    )
    return booked + held


class ProposalQueries:
    """The held proposals' part of ``SqlTransaction``; needs ``_session``."""

    _session: AsyncSession

    async def add_proposal(self, proposal: HeldProposal) -> HeldProposal:
        """Insert a held proposal and return it with its ID."""
        row = RescheduleProposalRow(
            booking_id=proposal.booking_id,
            slot_id=proposal.slot_id,
            start=proposal.start,
            end=proposal.end,
            confirm_by=proposal.confirm_by,
            created_at=proposal.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return HeldProposal(
            row.id,
            proposal.booking_id,
            proposal.slot_id,
            proposal.start,
            proposal.end,
            proposal.confirm_by,
            proposal.created_at,
        )

    async def get_open_proposal(self, booking_id: int) -> HeldProposal | None:
        """Return the booking's open proposal: not released, its booking awaiting a new time."""
        row = await self._session.scalar(
            select(RescheduleProposalRow)
            .join(BookingRow, BookingRow.id == RescheduleProposalRow.booking_id)
            .where(
                RescheduleProposalRow.booking_id == booking_id,
                RescheduleProposalRow.released_at.is_(None),
                BookingRow.status == _AWAITING,
            )
            .order_by(RescheduleProposalRow.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        return HeldProposal(
            row.id,
            row.booking_id,
            row.slot_id,
            row.start,
            row.end,
            row.confirm_by,
            row.created_at,
            row.released_at,
            ProposalOutcome(row.outcome) if row.outcome else None,
        )

    async def save_proposal(self, proposal: HeldProposal) -> None:
        """Persist a proposal's release."""
        await self._session.execute(
            update(RescheduleProposalRow)
            .where(RescheduleProposalRow.id == proposal.id)
            .values(
                released_at=proposal.released_at,
                outcome=proposal.outcome.value if proposal.outcome else None,
            )
        )
