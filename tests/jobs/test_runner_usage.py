"""A message job writes ``message.sent`` or ``message.failed`` and nothing about the person."""

import pytest

from app.domain.booking import Booking
from app.domain.channels import ChannelKind
from app.domain.job_plan import BACKOFF, JobType
from app.domain.message_usage import PURPOSES, record_message
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.usage import ActorClass, FactKind, UsageFact
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MESSAGE_KINDS, MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.jobs import FakeQueue, job
from tests.fakes.messaging import RecordingSender, RefusingSender
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, window


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = make_group(id=1, slug="balie")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    return InMemoryUnitOfWork(state)


@pytest.fixture
def composer() -> MessageComposer:
    return MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://localhost")


async def _booking(uow: InMemoryUnitOfWork) -> int:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0))
    return receipt.booking.id or 0


def _messages(uow: InMemoryUnitOfWork) -> list[UsageFact]:
    return [
        f
        for f in uow.state.usage_facts
        if isinstance(f, UsageFact) and f.kind in (FactKind.MESSAGE_SENT, FactKind.MESSAGE_FAILED)
    ]


def test_every_message_job_has_a_purpose_from_the_closed_list() -> None:
    assert set(MESSAGE_KINDS) == set(PURPOSES)
    assert set(PURPOSES.values()) == {
        "confirmation",
        "change",
        "reminder",
        "cancellation",
        "closure",
        "rebook",
        "confirm_request",
    }


async def test_a_sent_message_is_a_fact_with_channel_and_purpose(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await _booking(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)])

    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(), composer).run_due()

    (fact,) = _messages(uow)
    assert (fact.kind, fact.channel_kind, fact.reason_code) == (
        FactKind.MESSAGE_SENT,
        ChannelKind.EMAIL,
        "confirmation",
    )
    assert (fact.organisation_id, fact.group_id, fact.actor_class) == (1, 1, ActorClass.SYSTEM)
    assert "ada" not in repr(fact).lower()


async def test_a_message_that_is_retried_is_not_a_failure_until_it_gives_up(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await _booking(uow)
    queue = FakeQueue([job(bid, JobType.SEND_REMINDER, attempts=1)])
    runner = JobRunner(queue, uow, FixedClock(NOW), RecordingSender(failures=1), composer)
    await runner.run_due()
    assert _messages(uow) == []

    last = FakeQueue([job(bid, JobType.SEND_REMINDER, attempts=len(BACKOFF) + 1)])
    await JobRunner(last, uow, FixedClock(NOW), RecordingSender(failures=1), composer).run_due()

    (fact,) = _messages(uow)
    assert (fact.kind, fact.reason_code) == (FactKind.MESSAGE_FAILED, "reminder")


async def test_a_refused_message_is_one_failure_without_the_senders_text(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await _booking(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CANCELLATION)])

    await JobRunner(queue, uow, FixedClock(NOW), RefusingSender("unauthorized"), composer).run_due()

    (fact,) = _messages(uow)
    assert fact.kind is FactKind.MESSAGE_FAILED
    assert fact.reason_code == "cancellation"


async def test_a_skipped_job_and_a_team_invite_write_nothing(
    uow: InMemoryUnitOfWork, composer: MessageComposer
) -> None:
    bid = await _booking(uow)
    queue = FakeQueue([job(999, JobType.SEND_CONFIRMATION)])
    await JobRunner(queue, uow, FixedClock(NOW), RecordingSender(), composer).run_due()
    await record_message(uow, JobType.SEND_TEAM_INVITE, ChannelKind.EMAIL, bid, NOW, sent=True)
    await record_message(uow, JobType.SEND_CONFIRMATION, ChannelKind.EMAIL, 999, NOW, sent=True)

    assert _messages(uow) == []


async def test_a_failing_fact_never_changes_the_outcome_of_the_job(
    uow: InMemoryUnitOfWork, composer: MessageComposer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.jobs.runner as runner_module

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the store is gone")

    monkeypatch.setattr(runner_module, "record_message", broken)
    bid = await _booking(uow)
    queue = FakeQueue([job(bid, JobType.SEND_CONFIRMATION)])
    sender = RecordingSender()

    await JobRunner(queue, uow, FixedClock(NOW), sender, composer).run_due()

    assert queue.done == [1]
    assert len(sender.sent) == 1
    assert isinstance(uow.state.bookings[bid], Booking)
