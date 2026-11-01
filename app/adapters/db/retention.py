"""SQL queries of the retention overview (ADR 0039, ``retention.view``).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. They select counts, statuses and times only, never a personal data column or a
booking id, so nothing they return can identify a participant. Which booking is overdue is
decided in ``app.domain.retention``; the SQL only narrows the candidates.
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow, ClosureRow, JobRow, SlotRow
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.job_plan import JobType
from app.domain.retention import (
    DueBooking,
    GroupRetentionCounts,
    PurgeCandidate,
    RetentionFacts,
)
from app.domain.scheduling import GroupSummary


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        """Return the groups inside ``scope``."""
        ...


class RetentionQueries:
    """The retention overview's part of ``SqlTransaction``."""

    async def due_for_purge(self: _Transaction, candidates_before: datetime) -> list[DueBooking]:
        """Return the bookings of every group that hold personal data and may be overdue.

        The nightly sweep's selection, with ids and without a scope: the same rows as
        ``list_retention`` shows as candidates, for all groups.
        """
        return await _candidates(self._session, candidates_before, None)

    async def list_retention(
        self: _Transaction, scope: Scope, candidates_before: datetime
    ) -> RetentionFacts:
        """Return the counts per group in ``scope`` and the bookings that may be overdue.

        Candidates still hold personal data and either ended or were cancelled before
        ``candidates_before``, or are suspended (their closure decides).
        """
        groups = await self.list_groups(scope)
        ids = [g.group.id for g in groups if g.group.id is not None]
        if not ids:
            return RetentionFacts([], [])
        holding = BookingRow.pii_purged_at.is_(None)
        counts = await self._session.execute(
            select(
                BookingRow.group_id,
                func.sum(case((holding, 1), else_=0)),
                func.max(BookingRow.pii_purged_at),
            )
            .where(BookingRow.group_id.in_(ids))
            .group_by(BookingRow.group_id)
        )
        per_group = {group_id: (int(held or 0), last) for group_id, held, last in counts}
        failed = await self._session.execute(
            select(BookingRow.group_id, func.count(JobRow.id))
            .join(BookingRow, BookingRow.id == JobRow.booking_id)
            .where(
                BookingRow.group_id.in_(ids),
                holding,
                JobRow.type == JobType.PURGE_PII.value,
                JobRow.status == "failed",
            )
            .group_by(BookingRow.group_id)
        )
        failures = {group_id: int(count) for group_id, count in failed}
        due = await _candidates(self._session, candidates_before, ids)
        return RetentionFacts(
            [
                GroupRetentionCounts(
                    g,
                    per_group.get(g.group.id or 0, (0, None))[0],
                    failures.get(g.group.id or 0, 0),
                    per_group.get(g.group.id or 0, (0, None))[1],
                )
                for g in groups
            ],
            [d.candidate for d in due],
        )


async def _candidates(
    session: AsyncSession, candidates_before: datetime, group_ids: list[int] | None
) -> list[DueBooking]:
    """Return the bookings that still hold personal data and may be past their purge moment.

    Args:
        session: The caller's session.
        candidates_before: See ``app.domain.retention.candidates_before``.
        group_ids: Restrict to these groups; ``None`` for all.
    """
    where = [
        BookingRow.pii_purged_at.is_(None),
        or_(
            SlotRow.end < candidates_before,
            BookingRow.cancelled_at < candidates_before,
            BookingRow.status == BookingStatus.SUSPENDED.value,
            BookingRow.rebook_by < candidates_before,
            and_(
                BookingRow.status == BookingStatus.PENDING_CONFIRMATION.value,
                BookingRow.confirm_by < candidates_before,
            ),
        ),
    ]
    if group_ids is not None:
        where.append(BookingRow.group_id.in_(group_ids))
    rows = await session.execute(
        select(
            BookingRow.id,
            BookingRow.group_id,
            BookingRow.status,
            SlotRow.end,
            BookingRow.cancelled_at,
            ClosureRow.ends_at,
            BookingRow.rebook_by,
            BookingRow.confirm_by,
        )
        .join(SlotRow, SlotRow.id == BookingRow.slot_id)
        .outerjoin(ClosureRow, ClosureRow.id == BookingRow.closure_id)
        .where(*where)
        .order_by(BookingRow.id)
    )
    return [
        DueBooking(
            booking_id,
            PurgeCandidate(
                group_id,
                BookingStatus(status),
                end,
                cancelled_at,
                closure_end,
                rebook_by,
                confirm_by,
            ),
        )
        for (
            booking_id,
            group_id,
            status,
            end,
            cancelled_at,
            closure_end,
            rebook_by,
            confirm_by,
        ) in rows
    ]
