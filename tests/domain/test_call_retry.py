"""Planning a failed meeting or work item job again from the booking page (ADR 0068)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.authorization import Permission
from app.domain.booking import BookingStatus, MeetingRef
from app.domain.call_retry import CallRetryService
from app.domain.errors import DomainError
from app.domain.job_plan import JobType, PlannedJob
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, OUTSIDER, two_organisations
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, window

WIRED = {
    "video_host_email": "host@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
}


async def _setup() -> tuple[InMemoryUnitOfWork, CallRetryService, int]:
    state = two_organisations()
    state.groups[1] = replace(state.groups[1], **WIRED)  # type: ignore[arg-type]
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY), slug="demo")
    booking_id = receipt.booking.id or 0
    uow.state.jobs[booking_id] = []  # the first attempts ran and failed
    return uow, CallRetryService(uow, clock, AdminService(uow, clock)), booking_id


async def test_a_failed_meeting_is_planned_again_now_and_audited() -> None:
    uow, service, booking_id = await _setup()

    await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.CREATE_VIDEO_ROOM)
    await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.CREATE_VIDEO_ROOM)

    booking = uow.state.bookings[booking_id]
    assert uow.state.jobs[booking_id] == [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, NOW, booking.start)
    ]  # a second click replaces the pending job
    event = uow.state.audit[-1]
    assert (event.action, event.group, event.detail) == (
        Permission.BOOKINGS_MANAGE.value,
        "demo",
        "retry.create_video_room",
    )


async def test_a_work_item_is_planned_at_the_dispatch_moment() -> None:
    uow, service, booking_id = await _setup()
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(booking, meeting=MeetingRef("m", "https://x"))

    await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.DISPATCH_WXCC_TASK)

    [planned] = uow.state.jobs[booking_id]
    assert planned.run_at == booking.start - timedelta(minutes=5)


@pytest.mark.parametrize("principal", [OUTSIDER, AUDITOR])
async def test_only_who_manages_the_groups_bookings_may_retry(principal: object) -> None:
    _, service, booking_id = await _setup()

    with pytest.raises(DomainError, match="not_permitted"):
        await service.retry(principal, "demo", booking_id, JobType.CREATE_VIDEO_ROOM)  # type: ignore[arg-type]


async def test_nothing_is_retried_that_exists_is_not_retryable_or_is_cancelled() -> None:
    uow, service, booking_id = await _setup()
    booking = uow.state.bookings[booking_id]

    with pytest.raises(DomainError, match="nothing_to_retry"):
        await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.SEND_CONFIRMATION)
    uow.state.bookings[booking_id] = replace(booking, meeting=MeetingRef("m", "https://x"))
    with pytest.raises(DomainError, match="nothing_to_retry"):
        await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.CREATE_VIDEO_ROOM)
    uow.state.bookings[booking_id] = replace(booking, status=BookingStatus.CANCELLED)
    with pytest.raises(DomainError, match="booking_not_active"):
        await service.retry(GROUP_ADMIN, "demo", booking_id, JobType.CREATE_VIDEO_ROOM)
    with pytest.raises(DomainError, match="booking_not_found"):
        await service.retry(GROUP_ADMIN, "demo", 999, JobType.CREATE_VIDEO_ROOM)
    assert uow.state.jobs[booking_id] == []
