"""SQL queries of alert targets and deliveries (ADR 0082 point 9).

A mixin of ``SqlTransaction``. A target holds an administrator's email address or a Webex room
id, never participant data; a delivery the streak token, times and counters.
"""

from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AlertDeliveryRow, AlertTargetRow
from app.domain.alert_model import AlertChannelKind, AlertTarget, DeliveryState


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


def _target(row: AlertTargetRow) -> AlertTarget:
    """Map a target row to the domain."""
    return AlertTarget(
        row.id, row.organisation_id, AlertChannelKind(row.kind), row.address, row.min_level
    )


class AlertTargetQueries:
    """Reads and writes alert targets and what was delivered to them."""

    async def all_alert_targets(self: _Transaction) -> list[AlertTarget]:
        """Return every alert target, oldest first."""
        rows = await self._session.scalars(select(AlertTargetRow).order_by(AlertTargetRow.id))
        return [_target(row) for row in rows]

    async def list_alert_targets(
        self: _Transaction, organisation_id: int | None
    ) -> list[AlertTarget]:
        """Return the targets of one organisation, or of the platform (``None``)."""
        scope = (
            AlertTargetRow.organisation_id.is_(None)
            if organisation_id is None
            else AlertTargetRow.organisation_id == organisation_id
        )
        rows = await self._session.scalars(
            select(AlertTargetRow).where(scope).order_by(AlertTargetRow.id)
        )
        return [_target(row) for row in rows]

    async def add_alert_target(self: _Transaction, target: AlertTarget) -> int:
        """Store a new target and return its id."""
        row = AlertTargetRow(
            organisation_id=target.organisation_id,
            kind=target.kind.value,
            address=target.address,
            min_level=target.min_level,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def delete_alert_target(self: _Transaction, target_id: int) -> None:
        """Remove a target; its deliveries go with it (``ON DELETE CASCADE``, and here too)."""
        await self._session.execute(
            delete(AlertDeliveryRow).where(AlertDeliveryRow.target_id == target_id)
        )
        await self._session.execute(delete(AlertTargetRow).where(AlertTargetRow.id == target_id))

    async def list_alert_deliveries(self: _Transaction) -> list[DeliveryState]:
        """Return what was sent of each alert to each target."""
        rows = await self._session.scalars(select(AlertDeliveryRow))
        return [
            DeliveryState(
                row.target_id,
                row.alert_key,
                row.token,
                row.sent_at,
                row.suppressed,
                row.attempts,
                row.retry_at,
            )
            for row in rows
        ]

    async def save_alert_delivery(self: _Transaction, state: DeliveryState) -> None:
        """Create or replace what was sent of one alert to one target."""
        await self._session.merge(
            AlertDeliveryRow(
                target_id=state.target_id,
                alert_key=state.key[:255],
                token=state.token,
                sent_at=state.sent_at,
                suppressed=state.suppressed,
                attempts=state.attempts,
                retry_at=state.retry_at,
            )
        )
