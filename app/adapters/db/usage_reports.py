"""SQL for the usage reports: hourly cells and day counts, filtered by ``Scope`` (ADR 0117).

A mixin of ``SqlTransaction``. Every read goes through ``scope_filter`` on the group and its
organisation, the one translation of a scope into SQL (ADR 0014), and takes the group ids the
caller asks for on top, so a report never reads a group outside the reader's scope. The tables
hold counts and sums per hour and cut; nothing here reads a personal data column.
"""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    BookingDayCountRow,
    GroupRow,
    OrganisationRow,
    UsageHourRow,
)
from app.adapters.db.scope import scope_filter
from app.adapters.db.usage_rows import cell_of
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.usage import UsageCell
from app.domain.usage_report import DayStatusCount, ReportGroup


class UsageReportQueries:
    """The usage reports' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def list_report_groups(self, scope: Scope) -> list[ReportGroup]:
        """Return the groups inside ``scope``, archived ones too, by organisation and name."""
        rows = await self._session.execute(
            select(
                GroupRow.id,
                GroupRow.organisation_id,
                GroupRow.slug,
                GroupRow.name,
                OrganisationRow.slug,
                OrganisationRow.name,
                GroupRow.timezone,
            )
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            .order_by(OrganisationRow.name, GroupRow.name, GroupRow.id)
        )
        return [ReportGroup(*row) for row in rows]

    async def read_usage_cells(
        self, scope: Scope, group_ids: Sequence[int], first_hour: datetime, last_hour: datetime
    ) -> list[UsageCell]:
        """Return the hourly cells of ``group_ids`` inside ``scope`` in the half-open range."""
        rows = await self._session.scalars(
            select(UsageHourRow)
            .join(GroupRow, GroupRow.id == UsageHourRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
                UsageHourRow.group_id.in_(list(group_ids)),
                UsageHourRow.bucket_hour >= first_hour,
                UsageHourRow.bucket_hour < last_hour,
            )
            .order_by(UsageHourRow.bucket_hour, UsageHourRow.id)
        )
        return [cell_of(row) for row in rows]

    async def read_day_status_counts(
        self, scope: Scope, group_ids: Sequence[int], first_day: date, last_day: date
    ) -> list[DayStatusCount]:
        """Return the day counts (bookings from before reporting) of ``group_ids`` in ``scope``."""
        rows = await self._session.execute(
            select(
                BookingDayCountRow.group_id,
                BookingDayCountRow.day,
                BookingDayCountRow.status,
                BookingDayCountRow.count,
            )
            .join(GroupRow, GroupRow.id == BookingDayCountRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
                BookingDayCountRow.group_id.in_(list(group_ids)),
                BookingDayCountRow.day >= first_day,
                BookingDayCountRow.day <= last_day,
            )
        )
        return [DayStatusCount(g, d, BookingStatus(s), c) for g, d, s, c in rows]
