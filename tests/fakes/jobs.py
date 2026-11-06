"""The worker's job queue as the runner sees it (``app.jobs.runner.JobQueue``), and its jobs."""

from collections.abc import Collection
from datetime import datetime

from app.adapters.db.jobs import ClaimedJob
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType
from app.jobs.messages import MESSAGE_KINDS
from tests.support.domain import SLOT_0900


class FakeQueue:
    """``JobQueue`` fake: hands out its jobs once and records what became of each.

    ``claim_due`` returns the jobs of the asked types and empties the queue, so a second run
    finds nothing; ``requested_types`` keeps the types the runner asked for.
    """

    def __init__(self, jobs: list[ClaimedJob]) -> None:
        self.jobs = jobs
        self.requested_types: set[JobType] = set()
        self.done: list[int] = []
        self.retried: list[tuple[int, datetime, str]] = []
        self.failed: list[tuple[int, str]] = []

    async def claim_due(
        self,
        now: datetime,
        types: Collection[JobType],
        channels: Collection[ChannelKind],
        limit: int = 20,
    ) -> list[ClaimedJob]:
        self.requested_types = set(types)
        claimed = [j for j in self.jobs if j.type in types]
        self.jobs = []
        return claimed

    async def complete(self, job_id: int) -> None:
        self.done.append(job_id)

    async def retry(self, job_id: int, run_at: datetime, error: str) -> None:
        self.retried.append((job_id, run_at, error))

    async def fail(self, job_id: int, error: str) -> None:
        self.failed.append((job_id, error))


def job(
    booking_id: int, type_: JobType, attempts: int = 1, pinned: datetime | None = SLOT_0900
) -> ClaimedJob:
    """Return job 1 of ``type_`` for ``booking_id``, as claimed; a message goes out by email."""
    channel = ChannelKind.EMAIL if type_ in MESSAGE_KINDS else None
    return ClaimedJob(1, booking_id, type_, channel, pinned, attempts)
