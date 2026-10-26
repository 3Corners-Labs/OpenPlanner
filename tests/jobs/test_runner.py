from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.adapters.db.jobs import ClaimedJob
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MESSAGE_KINDS, MessageComposer
from app.jobs.runner import BACKOFF, JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.messaging import RecordingSender
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book, make_group, window


class FakeQueue:
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


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    return InMemoryUnitOfWork(state)


@pytest.fixture
def composer() -> MessageComposer:
    return MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://localhost")


def job(
    booking_id: int, type_: JobType, attempts: int = 1, pinned: datetime | None = SLOT_0900
) -> ClaimedJob:
    channel = ChannelKind.EMAIL if type_ in MESSAGE_KINDS else None
    return ClaimedJob(1, booking_id, type_, channel, pinned, attempts)


async def booking_id(uow: InMemoryUnitOfWork) -> int:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    return receipt.booking.id or 0


async def test_sends_confirmation(uow: InMemoryUnitOfWork, composer: MessageComposer) -> None:
    bid = await booking_id(uow)
    queue, sender = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)]), RecordingSender()

    assert await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due() == 1

    assert queue.done == [1]
    assert sender.sent[0].recipient == "ada@example.org"


async def test_without_sender_only_purges_are_claimed(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    queue = FakeQueue([])

    await JobRunner(queue, uow, FixedClock(NOW), None, composer).run_due()

    assert queue.requested_types == {JobType.PURGE_PII}


async def test_purge(uow: InMemoryUnitOfWork, composer: MessageComposer) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.PURGE_PII, pinned=None)])

    await JobRunner(queue, uow, FixedClock(NOW), None, composer).run_due()

    assert uow.state.bookings[bid].contact is None
    assert queue.done == [1]


@pytest.mark.parametrize("pinned", [SLOT_0900 - timedelta(days=1)])
async def test_stale_jobs_send_nothing(
    uow: InMemoryUnitOfWork, composer: MessageComposer, pinned: datetime
) -> None:
    bid = await booking_id(uow)
    queue, sender = FakeQueue([job(bid, JobType.SEND_REMINDER, pinned=pinned)]), RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.sent == []
    assert queue.done == [1]


async def test_missing_booking_sends_nothing(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    queue, sender = FakeQueue([job(999, JobType.SEND_CONFIRMATION)]), RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.sent == []
    assert queue.done == [1]


@pytest.mark.parametrize("attempt", [1, 2, 3])
async def test_failures_are_retried_with_backoff(
    uow: InMemoryUnitOfWork, composer: MessageComposer, attempt: int
) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION, attempts=attempt)])

    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(failures=1), composer).run_due()

    assert queue.retried == [(1, NOW + BACKOFF[attempt - 1], "ConnectionError")]
    assert queue.done == []


async def test_last_attempt_fails(uow: InMemoryUnitOfWork, composer: MessageComposer) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION, attempts=len(BACKOFF) + 1)])

    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(failures=1), composer).run_due()

    assert queue.failed == [(1, "ConnectionError")]


def sms_job(booking_id: int) -> ClaimedJob:
    return ClaimedJob(1, booking_id, JobType.SEND_REMINDER, ChannelKind.SMS, SLOT_0900, 1)


async def test_sms_reminder_goes_through_the_organisations_sms_sender(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    queue, sender = FakeQueue([sms_job(bid)]), RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.asked == [(1, ChannelKind.SMS)]
    assert sender.sent[0].channel is ChannelKind.SMS
    assert sender.sent[0].recipient == "0612345678"
    assert queue.done == [1]


async def test_group_texts_are_used(uow: InMemoryUnitOfWork, composer: MessageComposer) -> None:
    bid = await booking_id(uow)
    uow.state.group_texts[1] = {("nl", "message.confirmation.subject"): "Gelukt: {date}"}
    queue, sender = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)]), RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.sent[0].subject == "Gelukt: maandag 5 januari 2026"


async def test_an_unconfigured_channel_fails_at_once(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([sms_job(bid)])
    sender = RecordingSender(unconfigured=frozenset({ChannelKind.SMS}))

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.sent == []
    assert queue.failed == [(1, "channel_not_configured")]


async def test_sms_without_phone_number_is_skipped(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    booking = uow.state.bookings[bid]
    assert booking.contact is not None
    uow.state.bookings[bid] = replace(booking, contact=replace(booking.contact, phone=None))
    queue, sender = FakeQueue([sms_job(bid)]), RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert sender.sent == []
    assert queue.done == [1]
