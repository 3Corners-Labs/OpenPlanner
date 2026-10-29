"""SQL queries of the retention overview (ADR 0039, ``retention.view``).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. They select counts, statuses and times only, never a personal data column or a
booking id, so nothing they return can identify a citizen. Which booking is overdue is
decided in ``app.domain.retention``; the SQL only narrows the candidates.
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow, ClosureRow, JobRow, SlotRow
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.job_plan import JobType
from app.domain.retention import GroupRetentionCounts, PurgeCandidate, RetentionFacts
from app.domain.scheduling import GroupSummary


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        """Return the groups inside ``scope``."""
        ...


class RetentionQueries:
    """The retention overview's part of ``SqlTransaction``."""

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
        rows = await self._session.execute(
            select(
                BookingRow.group_id,
                BookingRow.status,
                SlotRow.end,
                BookingRow.cancelled_at,
                ClosureRow.ends_at,
                BookingRow.rebook_by,
            )
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .outerjoin(ClosureRow, ClosureRow.id == BookingRow.closure_id)
            .where(
                BookingRow.group_id.in_(ids),
                holding,
                or_(
                    SlotRow.end < candidates_before,
                    BookingRow.cancelled_at < candidates_before,
                    BookingRow.status == BookingStatus.SUSPENDED.value,
                    BookingRow.rebook_by < candidates_before,
                ),
            )
        )
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
            [
                PurgeCandidate(
                    group_id, BookingStatus(status), end, cancelled_at, closure_end, rebook_by
                )
                for group_id, status, end, cancelled_at, closure_end, rebook_by in rows
            ],
        )
