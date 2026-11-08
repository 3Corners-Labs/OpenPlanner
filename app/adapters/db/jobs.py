"""Job queue on the ``jobs`` table (HANDOVER B3): claim due jobs, then complete, retry or fail.

Claiming runs under the write lock, so two workers can never take the same job. A job that
stays ``running`` longer than ``STALE_CLAIM`` (worker crashed mid-job) becomes claimable again.

    pending ──claim──► running ──complete──► done
       ▲                  │ ──retry (backoff)──► pending
       └──── stale claim ─┘ ──fail (attempts exhausted)──► failed
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import JobRow
from app.adapters.db.uow import locked_session
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType

STALE_CLAIM = timedelta(minutes=10)
"""After this long a running job is considered abandoned by a crashed worker."""

MAX_ERROR_LENGTH = 255


@dataclass(frozen=True)
class ClaimedJob:
    """A job taken by this worker. Carries IDs only, never personal data."""

    id: int
    booking_id: int
    type: JobType
    channel: ChannelKind | None
    pinned_start: datetime | None
    attempts: int


class SqlJobQueue:
    """The ``jobs`` table as a queue.

    Args:
        engine: Engine created by ``app.adapters.db.engine.create_engine``.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Store the engine."""
        self._engine = engine

    async def claim_due(
        self,
        now: datetime,
        types: Collection[JobType],
        channels: Collection[ChannelKind],
        limit: int = 20,
    ) -> list[ClaimedJob]:
        """Claim up to ``limit`` due jobs of ``types`` whose channel is none or in ``channels``.

        Each claim increments ``attempts`` and records ``claimed_at``.
        """
        if not types:
            return []
        async with locked_session(self._engine) as session:
            rows = list(
                await session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.type.in_([t.value for t in types]),
                        or_(
                            JobRow.channel.is_(None),
                            JobRow.channel.in_([c.value for c in channels]),
                        ),
                        or_(
                            and_(JobRow.status == "pending", JobRow.run_at <= now),
                            and_(JobRow.status == "running", JobRow.claimed_at < now - STALE_CLAIM),
                        ),
                    )
                    .order_by(JobRow.run_at, JobRow.id)
                    .limit(limit)
                )
            )
            for row in rows:
                row.status = "running"
                row.claimed_at = now
                row.attempts += 1
            return [
                ClaimedJob(
                    id=row.id,
                    booking_id=row.booking_id,
                    type=JobType(row.type),
                    channel=ChannelKind(row.channel) if row.channel else None,
                    pinned_start=row.pinned_start,
                    attempts=row.attempts,
                )
                for row in rows
            ]

    async def complete(self, job_id: int) -> None:
        """Mark a claimed job as done."""
        await self._set(job_id, status="done", last_error=None)

    async def retry(self, job_id: int, run_at: datetime, error: str) -> None:
        """Put a failed attempt back in the queue for ``run_at``."""
        await self._set(job_id, status="pending", run_at=run_at, last_error=error)

    async def fail(self, job_id: int, error: str) -> None:
        """Give up on a job; it needs attention (alerting comes with WP8)."""
        await self._set(job_id, status="failed", last_error=error)

    async def _set(self, job_id: int, **values: object) -> None:
        """Update one job row; ``last_error`` is truncated and never holds personal data."""
        error = values.get("last_error")
        if isinstance(error, str):
            values["last_error"] = error[:MAX_ERROR_LENGTH]
        async with locked_session(self._engine) as session:
            await session.execute(update(JobRow).where(JobRow.id == job_id).values(**values))
