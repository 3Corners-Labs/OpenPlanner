"""SQL for rolling usage facts into the hourly table and pruning them (ADR 0117 point 4).

A mixin of ``SqlTransaction``, so every method runs in the caller's transaction: the cells and
the watermark that moves past the facts they came from are stored together, which is what makes
a crash neither double-count nor lose a fact. Nothing here reads a personal data column; the
tables hold none. The domain (``app.domain.usage_rollup``) decides what a fact becomes; the SQL
only reads, adds and deletes.
"""

from collections.abc import Sequence
from datetime import datetime

import structlog
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import UsageEventRow, UsageHourRow, UsageRollupStateRow
from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    ActorClass,
    FactKind,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
)
from app.domain.usage_rollup import UsageBatch, UsagePruned, UsageState

log = structlog.get_logger()

_STATE_ID = 1

_CUTS = (
    "booking_kind",
    "dispatch_kind",
    "subject_code",
    "locale",
    "channel_kind",
    "outcome",
    "actor_class",
    "reason_code",
    "bucket",
)
"""The cut columns of a cell, compared with ``IS`` so an empty cut matches an empty cut."""


def _enum[E](kind: type[E], value: str | None) -> E | None:
    """Return the enum member stored as ``value``; ``None`` stays ``None``."""
    return kind(value) if value is not None else None  # type: ignore[call-arg]


def _fact_of(row: UsageEventRow) -> UsageFact | StaffFact:
    """Rebuild the fact a row holds; a value this version does not know raises ``ValueError``."""
    kind = FactKind(row.kind)
    if row.staff_id is not None:
        return StaffFact(
            kind,
            row.bucket_hour,
            row.organisation_id,
            row.group_id,
            row.staff_id,
            row.value_seconds,
        )
    return UsageFact(
        kind,
        row.bucket_hour,
        row.organisation_id,
        row.group_id,
        booking_kind=_enum(BookingKind, row.booking_kind),
        dispatch_kind=_enum(DispatchKind, row.dispatch_kind),
        subject_code=row.subject_code,
        locale=row.locale,
        channel_kind=_enum(ChannelKind, row.channel_kind),
        outcome=_enum(UsageOutcome, row.outcome),
        actor_class=_enum(ActorClass, row.actor_class),
        reason_code=row.reason_code,
        value_seconds=row.value_seconds,
        participants=row.participants,
        capacity=row.capacity,
        booked=row.booked,
    )


def _plain(value: object) -> object:
    """Return an enum member as its value, anything else as it is."""
    return getattr(value, "value", value)


class UsageRollupQueries:
    """The rollup's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def read_usage_events(self, after_id: int, limit: int) -> UsageBatch:
        """Return up to ``limit`` facts after ``after_id``; a row of a newer version is skipped."""
        rows = list(
            await self._session.scalars(
                select(UsageEventRow)
                .where(UsageEventRow.id > after_id)
                .order_by(UsageEventRow.id)
                .limit(limit)
            )
        )
        facts: list[UsageFact | StaffFact] = []
        unreadable = 0
        for row in rows:
            try:
                facts.append(_fact_of(row))
            except ValueError:
                unreadable += 1
        return UsageBatch(tuple(facts), rows[-1].id if rows else after_id, unreadable)

    async def usage_state(self) -> UsageState:
        """Return the watermark, the time of the last run and how many facts wait."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        last = state.last_event_id if state is not None else 0
        pending = await self._session.scalar(
            select(func.count()).select_from(UsageEventRow).where(UsageEventRow.id > last)
        )
        return UsageState(last, state.rolled_at if state is not None else None, pending or 0)

    async def add_usage_cells(self, cells: Sequence[UsageCell]) -> None:
        """Add each cell to the row of its hour and cut, or insert it as a new row.

        There is no unique index over the cut columns (a NULL is never equal to a NULL in
        one): the rollup is the only writer, under the write lock, so a read then a write is
        enough.
        """
        for cell in cells:
            same = and_(
                UsageHourRow.bucket_hour == cell.bucket_hour,
                UsageHourRow.organisation_id == cell.organisation_id,
                UsageHourRow.group_id == cell.group_id,
                UsageHourRow.metric == cell.metric,
                *(
                    getattr(UsageHourRow, name).is_(None)
                    if getattr(cell, name) is None
                    else getattr(UsageHourRow, name) == _plain(getattr(cell, name))
                    for name in _CUTS
                ),
            )
            row = await self._session.scalar(select(UsageHourRow).where(same))
            if row is None:
                self._session.add(
                    UsageHourRow(
                        bucket_hour=cell.bucket_hour,
                        organisation_id=cell.organisation_id,
                        group_id=cell.group_id,
                        metric=cell.metric,
                        count=cell.count,
                        sum_value=cell.sum_value,
                        max_value=cell.max_value,
                        **{name: _plain(getattr(cell, name)) for name in _CUTS},
                    )
                )
            else:
                row.count += cell.count
                row.sum_value += cell.sum_value
                row.max_value = max(row.max_value, cell.max_value)
        await self._session.flush()

    async def advance_usage_watermark(self, last_event_id: int, at: datetime) -> None:
        """Move the watermark and the time of the last run, forwards only."""
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        if state is None:
            self._session.add(
                UsageRollupStateRow(id=_STATE_ID, last_event_id=last_event_id, rolled_at=at)
            )
        else:
            state.last_event_id = max(state.last_event_id, last_event_id)
            state.rolled_at = at
        await self._session.flush()

    async def prune_usage(self, events_before: datetime, hours_before: datetime) -> UsagePruned:
        """Delete facts already rolled up and older than ``events_before``, and old hourly rows.

        A fact the rollup has not read is never deleted, however old.
        """
        state = await self._session.get(UsageRollupStateRow, _STATE_ID)
        last = state.last_event_id if state is not None else 0
        events = await self._session.execute(
            delete(UsageEventRow).where(
                UsageEventRow.id <= last, UsageEventRow.bucket_hour < events_before
            )
        )
        hours = await self._session.execute(
            delete(UsageHourRow).where(UsageHourRow.bucket_hour < hours_before)
        )
        return UsagePruned(events.rowcount or 0, hours.rowcount or 0)  # type: ignore[attr-defined]
