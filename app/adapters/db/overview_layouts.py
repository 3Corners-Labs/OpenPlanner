"""SQL queries of the admins' own overview layouts (ADR 0096).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. A layout is
one row per staff member with the widget keys comma-separated; widget keys never contain a
comma (they are the register's identifiers).
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import OverviewLayoutRow, StaffRow

SEPARATOR = ","
"""Between two keys in ``widget_keys``."""


class OverviewLayoutQueries:
    """The overview layouts' part of ``SqlTransaction``; needs ``_session``."""

    _session: AsyncSession

    async def get_overview_layout(self, subject: str) -> tuple[str, ...] | None:
        """Return the own layout of ``subject``, or ``None`` without one."""
        found = await self._session.scalar(
            select(OverviewLayoutRow.widget_keys)
            .join(StaffRow, StaffRow.id == OverviewLayoutRow.staff_id)
            .where(StaffRow.subject == subject)
        )
        if found is None:
            return None
        return tuple(key for key in found.split(SEPARATOR) if key)

    async def save_overview_layout(
        self, subject: str, keys: tuple[str, ...] | None, at: datetime
    ) -> bool:
        """Store or delete the layout; ``False`` when ``subject`` has no staff record."""
        staff_id = await self._session.scalar(
            select(StaffRow.id).where(StaffRow.subject == subject)
        )
        if staff_id is None:
            return False
        if keys is None:
            await self._session.execute(
                delete(OverviewLayoutRow).where(OverviewLayoutRow.staff_id == staff_id)
            )
            return True
        row = await self._session.get(OverviewLayoutRow, staff_id)
        if row is None:
            self._session.add(
                OverviewLayoutRow(
                    staff_id=staff_id, widget_keys=SEPARATOR.join(keys), updated_at=at
                )
            )
        else:
            row.widget_keys = SEPARATOR.join(keys)
            row.updated_at = at
        await self._session.flush()
        return True
