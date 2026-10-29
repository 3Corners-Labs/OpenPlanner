"""SQL queries of the maintenance switch (ADR 0038).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. The table holds at most one row; without it, maintenance is off. A reason code that
no longer parses (a hand-edited row) is read as unknown rather than failing every request.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import MaintenanceStateRow
from app.domain.maintenance import OFF, MaintenanceReason, MaintenanceState

_STATE_ID = 1


def _reason(raw: str | None) -> MaintenanceReason | None:
    """Parse a stored reason code; anything unknown counts as no reason."""
    try:
        return MaintenanceReason(raw) if raw else None
    except ValueError:
        return None


class MaintenanceQueries:
    """The maintenance switch's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_maintenance_state(self) -> MaintenanceState:
        """Return the stored switch; ``OFF`` when it was never switched."""
        row = await self._session.get(MaintenanceStateRow, _STATE_ID)
        if row is None or not row.active:
            return OFF
        return MaintenanceState(
            True, row.since, row.switched_by, row.expected_end, _reason(row.reason)
        )

    async def save_maintenance_state(self, state: MaintenanceState) -> None:
        """Replace the single row with ``state``."""
        await self._session.merge(
            MaintenanceStateRow(
                id=_STATE_ID,
                active=state.on,
                since=state.since,
                switched_by=state.by,
                expected_end=state.expected_end,
                reason=state.reason.value if state.reason else None,
            )
        )
        await self._session.flush()
