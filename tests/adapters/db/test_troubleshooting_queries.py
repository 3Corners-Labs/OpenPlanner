"""Jobs, request events, timeline versions and the worker heartbeat in SQLite (ADR 0068)."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AuditEvent, AuditFilter, Category, Outcome, Source
from app.domain.authorization import Scope
from app.domain.calls import BookingEvent, EventKind
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobCounts, JobStatus, JobType
from app.domain.request_log import REQUEST_EVENT
from app.domain.services import BookingService
from app.domain.worker_check import WorkerHeartbeat
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book


async def _booked(engine: AsyncEngine) -> tuple[int, int]:
    group_id = await seed_group(engine)
    receipt = await book(BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY))
    return group_id, receipt.booking.id or 0


async def test_the_bookings_jobs_are_listed_with_status_and_error(engine: AsyncEngine) -> None:
    _, booking_id = await _booked(engine)
    queue = SqlJobQueue(engine)
    [meeting] = await queue.claim_due(NOW, {JobType.CREATE_VIDEO_ROOM}, set())
    await queue.fail(meeting.id, "rejected_400")

    async with SqlUnitOfWork(engine).read() as tx:
        jobs = await tx.get_booking_jobs(booking_id)

    failed = next(j for j in jobs if j.type is JobType.CREATE_VIDEO_ROOM)
    assert (failed.status, failed.attempts, failed.last_error) == (
        JobStatus.FAILED,
        1,
        "rejected_400",
    )
    confirmation = next(j for j in jobs if j.type is JobType.SEND_CONFIRMATION)
    assert (confirmation.status, confirmation.channel) == (JobStatus.PENDING, ChannelKind.EMAIL)
    assert [j.run_at for j in jobs] == sorted(j.run_at for j in jobs)


async def test_a_groups_failed_and_overdue_jobs_are_counted(engine: AsyncEngine) -> None:
    group_id, _ = await _booked(engine)
    queue = SqlJobQueue(engine)
    [meeting] = await queue.claim_due(NOW, {JobType.CREATE_VIDEO_ROOM}, set())
    await queue.fail(meeting.id, "rejected_400")
    uow = SqlUnitOfWork(engine)

    async with uow.read() as tx:
        now = await tx.count_group_jobs(group_id, NOW)
        later = await tx.count_group_jobs(group_id, NOW + timedelta(minutes=1))
        other = await tx.count_group_jobs(group_id + 999, NOW + timedelta(minutes=1))

    assert now == JobCounts(failed=1, overdue=0)
    assert later == JobCounts(failed=1, overdue=1)  # the confirmation waits since NOW
    assert other == JobCounts()


async def test_a_timeline_event_keeps_its_version(engine: AsyncEngine) -> None:
    _, booking_id = await _booked(engine)
    uow = SqlUnitOfWork(engine)
    event = BookingEvent(booking_id, NOW, EventKind.MEETING_CREATED, version="0.4.0-rc.7")

    async with uow.locked() as tx:
        await tx.add_booking_event(event)
    async with uow.read() as tx:
        assert await tx.get_booking_events(booking_id) == [event]


async def test_request_events_are_filtered_and_pruned_by_action(engine: AsyncEngine) -> None:
    await seed_group(engine)
    uow = SqlUnitOfWork(engine)
    request = AuditEvent(
        NOW, "system", REQUEST_EVENT, Outcome.SUCCESS, source=Source.SYSTEM, booking_id=7
    )
    job = AuditEvent(
        NOW, "system", "job.create_video_room", Outcome.SUCCESS, source=Source.SYSTEM, booking_id=7
    )
    async with uow.locked() as tx:
        await tx.add_audit_event(request)
        await tx.add_audit_event(job)

    async with uow.read() as tx:
        found = await tx.list_audit_events(
            Scope(everything=True), 10, AuditFilter(booking_id=7, action=REQUEST_EVENT)
        )
    assert [e.event.action for e in found] == [REQUEST_EVENT]

    later = NOW + timedelta(seconds=1)
    async with uow.locked() as tx:
        assert await tx.delete_audit_events(Category.SYSTEM, later, REQUEST_EVENT) == 1
    async with uow.read() as tx:
        left = await tx.list_audit_events(Scope(everything=True), 10)
    assert [e.event.action for e in left] == ["job.create_video_room"]


async def test_the_worker_heartbeat_round_trips_and_is_replaced(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        assert await tx.get_worker_heartbeat() is None

    first = WorkerHeartbeat(NOW, "0.4.0-rc.7", ("create_video_room", "purge_pii"))
    second = WorkerHeartbeat(NOW + timedelta(minutes=1), "0.4.0-rc.8", ())
    for heartbeat in (first, second):
        async with uow.locked() as tx:
            await tx.save_worker_heartbeat(heartbeat)
        async with uow.read() as tx:
            assert await tx.get_worker_heartbeat() == heartbeat
