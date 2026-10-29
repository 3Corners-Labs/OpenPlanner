from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from structlog.testing import capture_logs
from structlog.typing import EventDict, WrappedLogger

from app.adapters.db.jobs import ClaimedJob
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
from app.domain.event_context import TRACE_ID, current, current_trace_id
from app.domain.job_plan import JobType
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.system_log import SystemLog
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MESSAGE_KINDS, MessageComposer
from app.jobs.runner import BACKOFF, JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.messaging import RecordingSender, RefusingSender
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.closures import NOTICES
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book, make_group, window


def merge_context(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    context = current()
    return {**event_dict, "trace_id": context.trace_id, "group": context.group}


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


async def test_without_sender_only_purges_and_lapses_are_claimed(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    queue = FakeQueue([])

    await JobRunner(queue, uow, FixedClock(NOW), None, composer).run_due()

    assert queue.requested_types == {JobType.PURGE_PII, JobType.LAPSE_REBOOK}


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


async def test_closure_notice_goes_only_to_bookings_on_hold(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    closure = Closure(7, 1, NOW, None, NOTICES, NOW)
    uow.state.closures[7] = closure
    uow.state.bookings[bid] = uow.state.bookings[bid].suspend(7)
    queue = FakeQueue([job(bid, JobType.SEND_SUSPENSION, pinned=None)])
    sender = RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()
    assert NOTICES["nl"] in sender.sent[0].text

    uow.state.bookings[bid] = uow.state.bookings[bid].reinstate()
    queue.jobs = [job(bid, JobType.SEND_CLOSURE_UPDATE, pinned=None)]
    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()
    assert len(sender.sent) == 1  # reinstated in the meantime: the update is stale


def system_events(uow: InMemoryUnitOfWork) -> list[tuple[str, Outcome, str, str, str | None]]:
    return [
        (e.action, e.outcome, e.reason, e.detail, e.group)
        for e in uow.state.audit
        if e.source is Source.SYSTEM
    ]


async def test_a_sent_message_is_a_system_event_of_the_group(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    # ADR 0031: the group's admins can see that the reminder went out, and on which channel.
    bid = await booking_id(uow)
    queue = FakeQueue([sms_job(bid)])

    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(), composer).run_due()

    assert system_events(uow) == [
        ("job.send_reminder", Outcome.SUCCESS, "", "attempt:1,channel:sms", "aangifte")
    ]
    assert uow.state.audit[-1].booking_id == bid


async def test_a_stale_job_is_recorded_as_skipped_with_the_reason(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.SEND_REMINDER, pinned=SLOT_0900 - timedelta(days=1))])

    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(), composer).run_due()

    assert system_events(uow) == [
        ("job.send_reminder", Outcome.SKIPPED, "stale", "attempt:1,channel:email", "aangifte")
    ]


async def test_retries_and_the_final_failure_are_recorded_with_the_error_code(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue(
        [
            job(bid, JobType.SEND_CONFIRMATION, attempts=1),
            job(bid, JobType.SEND_CONFIRMATION, attempts=len(BACKOFF) + 1),
        ]
    )
    sender = RefusingSender("smtp_unreachable", retryable=True)

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert [(action, outcome, reason) for action, outcome, reason, _, _ in system_events(uow)] == [
        ("job.send_confirmation", Outcome.RETRY, "smtp_unreachable"),
        ("job.send_confirmation", Outcome.FAILURE, "smtp_unreachable"),
    ]
    assert system_events(uow)[1][3] == "attempt:4,channel:email"


@pytest.mark.parametrize("method", ["record", "bind_level"])
async def test_a_failing_system_log_never_stops_a_job(
    uow: InMemoryUnitOfWork,
    composer: MessageComposer,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    bid = await booking_id(uow)
    queue, sender = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)]), RecordingSender()

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database locked")

    monkeypatch.setattr(SystemLog, method, broken)
    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert queue.done == [1]
    assert len(sender.sent) == 1


async def test_each_job_runs_in_its_own_trace_tagged_with_its_group(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    # ADR 0040, part 2: the job's log lines and its system event share one trace id.
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION), job(bid, JobType.SEND_REMINDER)])

    with capture_logs(processors=[merge_context]) as logs:
        await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(), composer).run_due()

    first, second = (e for e in uow.state.audit if e.source is Source.SYSTEM)
    assert TRACE_ID.fullmatch(first.trace_id)
    assert first.trace_id != second.trace_id
    sent = [entry for entry in logs if entry["event"] == "job.sent"]
    assert [entry["trace_id"] for entry in sent] == [first.trace_id, second.trace_id]
    assert {entry["group"] for entry in sent} == {"aangifte"}
    assert current_trace_id() == ""


async def test_old_system_events_are_pruned_at_most_once_an_hour(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    old = AuditEvent(
        NOW - timedelta(days=31),
        SYSTEM_ACTOR,
        "job.purge_pii",
        Outcome.SUCCESS,
        source=Source.SYSTEM,
    )
    uow.state.audit = [old]
    clock = FixedClock(NOW)
    runner = JobRunner(FakeQueue([]), uow, clock, None, composer, retention=timedelta(days=30))

    await runner.run_due()
    assert uow.state.audit == []

    uow.state.audit = [old]
    clock.advance(timedelta(minutes=59))
    await runner.run_due()
    assert uow.state.audit == [old]

    clock.advance(timedelta(minutes=1))
    await runner.run_due()
    assert uow.state.audit == []


async def _awaiting(uow: InMemoryUnitOfWork, bid: int) -> None:
    booking = uow.state.bookings[bid]
    uow.state.bookings[bid] = booking.request_rebook(
        ChangeReason.STAFFING, SLOT_0900 + timedelta(days=14)
    )


async def test_an_unanswered_request_to_rebook_lapses(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    await _awaiting(uow, bid)
    queue = FakeQueue([job(bid, JobType.LAPSE_REBOOK, pinned=None)])

    await JobRunner(queue, uow, FixedClock(NOW), None, composer).run_due()

    assert uow.state.bookings[bid].status is BookingStatus.CANCELLED
    assert any(j.type is JobType.SEND_REBOOK_LAPSED for j in uow.state.jobs[bid])
    assert queue.done == [1]


async def test_a_lapse_after_a_new_time_was_chosen_is_skipped(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    queue = FakeQueue([job(bid, JobType.LAPSE_REBOOK, pinned=None)])

    await JobRunner(queue, uow, FixedClock(NOW), None, composer).run_due()

    assert uow.state.bookings[bid].status is BookingStatus.CONFIRMED
    (event,) = [e for e in uow.state.audit if e.action == "job.lapse_rebook"]
    assert event.outcome is Outcome.SKIPPED


async def test_the_request_to_rebook_goes_only_while_it_is_open(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await booking_id(uow)
    await _awaiting(uow, bid)
    sender = RecordingSender()
    request = job(bid, JobType.SEND_REBOOK_REQUEST, pinned=None)

    await JobRunner(FakeQueue([request]), uow, FixedClock(NOW), sender, composer).run_due()
    uow.state.bookings[bid] = uow.state.bookings[bid].cancel(NOW)
    await JobRunner(FakeQueue([request]), uow, FixedClock(NOW), sender, composer).run_due()

    assert len(sender.sent) == 1
    assert sender.sent[0].subject == "Kies een nieuwe tijd voor uw videogesprek"
