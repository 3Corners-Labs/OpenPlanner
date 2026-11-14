"""Purging a booking in a transaction: what the adapter stores and what follows (ADR 0102, 0123).

The adapter's ``purge_personal_data`` only stores. This module is the one place that decides what
a purge adds (``retention.jobs_after_purge``) and what it must leave pending
(``job_plan.OUTLIVES_PURGE``); the ``purge_pii`` job and the sweep both call it.
"""

from datetime import datetime

from app.domain.job_plan import OUTLIVES_PURGE, JobType
from app.domain.ports import Transaction
from app.domain.retention import jobs_after_purge


async def purge_booking(tx: Transaction, booking_id: int, now: datetime) -> None:
    """Purge a booking's personal data and add what follows, in the caller's transaction.

    The one entry the ``purge_pii`` job and the sweep use: the adapter only stores
    (``purge_personal_data``), this decides what follows (``jobs_after_purge``). What the purge
    must not delete is ``OUTLIVES_PURGE``.
    """
    booking = await tx.get_booking(booking_id)
    await tx.purge_personal_data(booking_id, now)
    await tx.add_jobs(booking_id, jobs_after_purge(booking, now))


async def delete_jobs_of_purged(tx: Transaction, booking_id: int) -> None:
    """Delete every pending job of a purged booking except the ones that outlive the purge."""
    await tx.delete_pending_jobs(booking_id, set(JobType) - OUTLIVES_PURGE)
