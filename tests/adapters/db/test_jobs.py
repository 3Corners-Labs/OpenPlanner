from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.jobs import STALE_CLAIM, SqlJobQueue
from app.adapters.db.models import JobRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType
from app.domain.services import BookingService
from app.domain.tasks import JobState
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book

EMAIL_JOBS = {JobType.SEND_CONFIRMATION, JobType.SEND_REMINDER}


async def booked(engine: AsyncEngine) -> SqlJobQueue:
    await seed_group(engine)
    await book(BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY))
    return SqlJobQueue(engine)


async def statuses(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(JobRow.type, JobRow.status))
        return {t: s for t, s in rows}


async def test_claims_only_due_jobs_of_the_requested_types(engine: AsyncEngine) -> None:
    queue = await booked(engine)

    claimed = await queue.claim_due(NOW, EMAIL_JOBS, {ChannelKind.EMAIL})

    assert [j.type for j in claimed] == [JobType.SEND_CONFIRMATION]  # the reminder is not due yet
    assert claimed[0].attempts == 1
    assert claimed[0].channel is ChannelKind.EMAIL
    assert claimed[0].pinned_start == SLOT_0900
    assert (await statuses(engine))[JobType.SEND_CONFIRMATION] == "running"
    assert await queue.claim_due(NOW, EMAIL_JOBS, {ChannelKind.EMAIL}) == []  # no double claim


async def test_channel_filter(engine: AsyncEngine) -> None:
    queue = await booked(engine)

    assert await queue.claim_due(NOW, EMAIL_JOBS, {ChannelKind.SMS}) == []
    assert await queue.claim_due(NOW, set(), {ChannelKind.EMAIL}) == []


async def test_complete_retry_fail(engine: AsyncEngine) -> None:
    queue = await booked(engine)
    later = SLOT_0900 - timedelta(hours=24)
    [confirmation] = await queue.claim_due(NOW, EMAIL_JOBS, {ChannelKind.EMAIL})
    await queue.complete(confirmation.id)
    [reminder] = await queue.claim_due(later, EMAIL_JOBS, {ChannelKind.EMAIL})

    await queue.retry(reminder.id, later + timedelta(minutes=1), "TimeoutError")
    assert await queue.claim_due(later, EMAIL_JOBS, {ChannelKind.EMAIL}) == []
    [again] = await queue.claim_due(later + timedelta(minutes=1), EMAIL_JOBS, {ChannelKind.EMAIL})
    await queue.fail(again.id, "x" * 1000)

    assert again.attempts == 2
    assert await statuses(engine) == {
        **await statuses(engine),
        JobType.SEND_CONFIRMATION: "done",
        JobType.SEND_REMINDER: "failed",
    }
    async with engine.connect() as conn:
        error = (
            await conn.execute(select(JobRow.last_error).where(JobRow.id == again.id))
        ).scalar_one()
    assert len(error) == 255


async def test_stale_claims_are_recovered(engine: AsyncEngine) -> None:
    queue = await booked(engine)
    [job] = await queue.claim_due(NOW, EMAIL_JOBS, {ChannelKind.EMAIL})

    assert await queue.claim_due(NOW + STALE_CLAIM, EMAIL_JOBS, {ChannelKind.EMAIL}) == []
    [recovered] = await queue.claim_due(
        NOW + STALE_CLAIM + timedelta(seconds=1), EMAIL_JOBS, {ChannelKind.EMAIL}
    )

    assert recovered.id == job.id
    assert recovered.attempts == 2


async def test_active_jobs_lists_running_then_failed_jobs_with_their_count(
    engine: AsyncEngine,
) -> None:
    queue = await booked(engine)
    [video, confirmation] = await queue.claim_due(
        NOW, {JobType.CREATE_VIDEO_ROOM, JobType.SEND_CONFIRMATION}, {ChannelKind.EMAIL}
    )
    await queue.fail(video.id, "rejected_400")

    async with SqlUnitOfWork(engine).read() as tx:
        active = await tx.active_jobs(limit=5)
        first_only = await tx.active_jobs(limit=1)

    assert [(j.type, j.state) for j in active.jobs] == [
        (JobType.SEND_CONFIRMATION, JobState.RUNNING),
        (JobType.CREATE_VIDEO_ROOM, JobState.FAILED),
    ]
    assert active.total == 2
    assert active.jobs[0].since == NOW
    assert active.jobs[0].attempts == 1
    assert [j.type for j in first_only.jobs] == [JobType.SEND_CONFIRMATION]
    assert first_only.total == 2
    assert not any(
        hasattr(j, "booking_id") for j in active.jobs
    )  # no payload, no link to a participant
