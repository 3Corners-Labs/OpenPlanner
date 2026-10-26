"""System events (ADR 0029): what the platform did on its own, at the level it happened."""

from datetime import timedelta

import pytest

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, window


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="aangifte")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    return InMemoryUnitOfWork(state)


async def test_a_booking_event_lands_on_the_bookings_group(uow: InMemoryUnitOfWork) -> None:
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    booking_id = receipt.booking.id

    await SystemLog(uow, FixedClock(NOW)).record(
        "job.send_reminder", Outcome.RETRY, booking_id=booking_id, reason="timeout", detail="x:1"
    )

    assert uow.state.audit == [
        AuditEvent(
            NOW,
            SYSTEM_ACTOR,
            "job.send_reminder",
            Outcome.RETRY,
            "timeout",
            "demo",
            "aangifte",
            "x:1",
            Source.SYSTEM,
            booking_id,
        )
    ]


@pytest.mark.parametrize("booking_id", [None, 999])
async def test_without_a_known_booking_the_event_is_global(
    uow: InMemoryUnitOfWork, booking_id: int | None
) -> None:
    await SystemLog(uow, FixedClock(NOW)).record(
        "job.purge_pii", Outcome.SKIPPED, booking_id=booking_id
    )

    [event] = uow.state.audit
    assert (event.organisation, event.group, event.booking_id) == (None, None, booking_id)
    assert event.source is Source.SYSTEM


async def test_prune_removes_old_system_events_only(uow: InMemoryUnitOfWork) -> None:
    old = NOW - timedelta(days=91)
    uow.state.audit = [
        AuditEvent(old, SYSTEM_ACTOR, "job.send_reminder", Outcome.SUCCESS, source=Source.SYSTEM),
        AuditEvent(old, "o-1", "group.edit", Outcome.SUCCESS),
        AuditEvent(NOW, SYSTEM_ACTOR, "job.send_reminder", Outcome.SUCCESS, source=Source.SYSTEM),
    ]

    removed = await SystemLog(uow, FixedClock(NOW)).prune(timedelta(days=90))

    assert removed == 1
    assert [(e.at, e.source) for e in uow.state.audit] == [
        (old, Source.ADMIN),
        (NOW, Source.SYSTEM),
    ]
