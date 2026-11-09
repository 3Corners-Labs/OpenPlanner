"""SQL for a team member's hourly cells: roll up, read, and fold on removal (ADR 0117 point 9).

A mixin of ``SqlTransaction``. ``usage_staff_hour`` is the one table that holds a ``staff_id``;
the rollup adds to it, the Team report reads it inside the reader's ``Scope`` (ADR 0014), and the
removal of a team member folds the member's rows into *former team members* (``staff_id`` empty)
in the same transaction as the removal. What a fact becomes is decided by
``app.domain.usage_staff``; the SQL only reads, adds and updates.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    GroupRow,
    OrganisationRow,
    StaffRow,
    UsageEventRow,
    UsageRollupStateRow,
    UsageStaffHourRow,
)
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.usage import FactKind, StaffFact
from app.domain.usage_staff import StaffCell, facts_folded, fold

_STATE_ID = 1


def _cell_of(row: UsageStaffHourRow) -> StaffCell:
    """Return the cell a stored row holds, its hour as UTC with tzinfo."""
    hour = row.bucket_hour if row.bucket_hour.tzinfo else row.bucket_hour.replace(tzinfo=UTC)
    return StaffCell(
        hour,
        row.organisation_id,
        row.group_id,
        row.staff_id,
        row.metric,
        row.count,
        row.sum_value,
        row.max_value,
    )


class UsageStaffQueries:
    """The team members' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def add_staff_cells(self, cells: Sequence[StaffCell]) -> None:
        """Add each cell to the row of its hour, group, member and metric, or insert it.

        The rollup and the removal of a member are the only writers, both under the write lock,
        so a read then a write is enough.
        """
        for cell in cells:
            same = and_(
                UsageStaffHourRow.bucket_hour == cell.bucket_hour,
                UsageStaffHourRow.organisation_id == cell.organisation_id,
                UsageStaffHourRow.group_id == cell.group_id,
                UsageStaffHourRow.metric == cell.metric,
                UsageStaffHourRow.staff_id.is_(None)
                if cell.staff_id is None
                else UsageStaffHourRow.staff_id == cell.staff_id,
            )
            row = await self._session.scalar(select(UsageStaffHourRow).where(same))
            if row is None:
                self._session.add(
                    UsageStaffHourRow(
                        bucket_hour=cell.bucket_hour,
                        organisation_id=cell.organisation_id,
                        group_id=cell.group_id,
                        staff_id=cell.staff_id,
                        metric=cell.metric,
                        count=cell.count,
                        sum_value=cell.sum_value,
                        max_value=cell.max_value,
                    )
                )
            else:
                row.count += cell.count
                row.sum_value += cell.sum_value
                row.max_value = max(row.max_value, cell.max_value)
        await self._session.flush()

    async def read_staff_cells(
        self, scope: Scope, group_ids: Sequence[int], first_hour: datetime, last_hour: datetime
    ) -> list[StaffCell]:
        """Return the team cells of ``group_ids`` inside ``scope`` in the half-open hour range."""
        rows = await self._session.scalars(
            select(UsageStaffHourRow)
            .join(GroupRow, GroupRow.id == UsageStaffHourRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
                UsageStaffHourRow.group_id.in_(list(group_ids)),
                UsageStaffHourRow.bucket_hour >= first_hour,
                UsageStaffHourRow.bucket_hour < last_hour,
            )
            .order_by(UsageStaffHourRow.bucket_hour, UsageStaffHourRow.id)
        )
        return [_cell_of(row) for row in rows]

    async def read_staff_names(self, staff_ids: Sequence[int]) -> dict[int, str]:
        """Return the display name of each of ``staff_ids`` that still has one."""
        if not staff_ids:
            return {}
        rows = await self._session.execute(
            select(StaffRow.id, StaffRow.display_name).where(StaffRow.id.in_(list(staff_ids)))
        )
        return {i: name for i, name in rows if name}

    async def fold_staff_usage(self, staff_id: int) -> None:
        """Fold everything about ``staff_id`` into *former team members*; the totals stay.

        The member's hourly rows lose their ``staff_id`` and are added to the former members' row
        of the same hour, group and metric. A fact still waiting for the rollup is turned into
        such a cell here and deleted, so the rollup never sees the person; a fact the rollup has
        read is deleted too, since the outbox would otherwise keep the id until retention.
        """
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        watermark = state.last_event_id if state is not None else 0
        events = list(
            await self._session.scalars(
                select(UsageEventRow).where(UsageEventRow.staff_id == staff_id)
            )
        )
        waiting = [
            StaffFact(
                FactKind(e.kind),
                e.bucket_hour,
                e.organisation_id,
                e.group_id,
                staff_id,
                e.value_seconds,
            )
            for e in events
            if e.id > watermark
        ]
        await self._session.execute(delete(UsageEventRow).where(UsageEventRow.staff_id == staff_id))
        owned = list(
            await self._session.scalars(
                select(UsageStaffHourRow).where(UsageStaffHourRow.staff_id == staff_id)
            )
        )
        moved = [_cell_of(row) for row in owned]
        await self._session.execute(
            delete(UsageStaffHourRow).where(UsageStaffHourRow.staff_id == staff_id)
        )
        await self._session.flush()
        await self.add_staff_cells([*fold(moved), *facts_folded(waiting)])
