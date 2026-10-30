"""Running tasks: what the ``jobs`` table is busy with or gave up on (ADR 0061 §2).

The *Running tasks* widget of the overview shows the background jobs a worker is running and
the ones that failed after their last attempt, so an admin sees a stuck or broken job engine
without opening the logs. A job is described by its type, state and moment only: never its
payload, never the booking it belongs to (CLAUDE.md: no PII on the overview). Imports and
exports will appear here once they run as jobs.

Pure Python, no I/O; ``Transaction.active_jobs`` loads them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.job_plan import JobType


class JobState(StrEnum):
    """The states of a job the widget shows; ``pending`` and ``done`` are not active."""

    RUNNING = "running"
    FAILED = "failed"


@dataclass(frozen=True)
class ActiveJob:
    """One running or failed job, without its payload.

    Attributes:
        type: What the job does.
        state: Running, or failed after its last attempt.
        since: When it was claimed (running) or was due (failed), UTC.
        attempts: How many times a worker tried it.
    """

    type: JobType
    state: JobState
    since: datetime
    attempts: int


@dataclass(frozen=True)
class ActiveJobs:
    """The first active jobs and how many there are in all."""

    jobs: list[ActiveJob]
    total: int
