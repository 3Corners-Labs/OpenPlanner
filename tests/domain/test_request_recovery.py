"""Work in recovery: the supervisors' list and the alert while any exists (ADR 0121 step 5e).

On the in-memory store with the world of ``test_request_work``: an appointment of a flow
without video is claimed and started at 09:00, and the watchdog marks it for recovery a day
after its end. Its booking has ended, so the queue no longer lists it as a row (design R9).
"""

from datetime import timedelta

from app.domain.alerts import RECOVERY_EVENT, RECOVERY_REASON
from app.domain.audit import Outcome, Source
from app.domain.request_recovery import RecoveryWatch
from app.domain.request_watch import RequestWatch
from app.domain.service_requests import HandlingState
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, SLOT_0900
from tests.support.request_work import ADMIN, AGENT, LENGTH, POOL, at, book, queue_of, world

DEADLINE = SLOT_0900 + LENGTH + timedelta(hours=24)


async def _in_recovery() -> tuple[InMemoryUnitOfWork, FixedClock, int]:
    """Claim and start an appointment, then pass its deadline; return the store and booking."""
    uow, clock = world(), FixedClock(NOW)
    booking_id = await book(uow, clock)
    at(clock)
    queue = queue_of(uow, clock)
    await queue.claim(AGENT, "demo", booking_id)
    await queue.start(AGENT, "demo", booking_id)
    clock.advance(DEADLINE - clock.now())
    await RequestWatch(uow, clock, 24).run()
    request = next(iter(uow.state.requests.values()))
    assert request.handling is HandlingState.RECOVERY_REQUIRED
    return uow, clock, booking_id


def _watch(uow: InMemoryUnitOfWork, clock: FixedClock, reported: set[str]) -> RecoveryWatch:
    async def open_alerts() -> set[str]:
        return set(reported)

    return RecoveryWatch(uow, SystemLog(uow, clock), open_alerts)


def _events(uow: InMemoryUnitOfWork) -> list[tuple[Outcome, str, str]]:
    return [
        (e.outcome, e.reason, e.detail)
        for e in uow.state.audit
        if e.action == RECOVERY_EVENT and e.source is Source.SYSTEM
    ]


async def test_a_supervisor_sees_work_in_recovery_that_the_queue_no_longer_lists() -> None:
    uow, clock, booking_id = await _in_recovery()
    queue = queue_of(uow, clock)

    supervisor = await queue.view(ADMIN, "demo")
    agent = await queue.view(AGENT, "demo")

    assert [r.booking_id for r in supervisor.rows] == []  # its appointment has ended
    (work,) = supervisor.recovery
    assert (work.booking_id, work.reference) == (booking_id, f"OP-{booking_id:06d}")
    assert work.started_at == SLOT_0900
    assert work.watchdog_at == DEADLINE
    assert agent.recovery == ()  # the list is for whoever supervises the queue


async def test_a_supervisor_completes_it_and_the_unit_is_released_once() -> None:
    uow, clock, booking_id = await _in_recovery()
    queue = queue_of(uow, clock)

    await queue.complete(ADMIN, "demo", booking_id)

    assert (await queue.view(ADMIN, "demo")).recovery == ()
    async with uow.read() as tx:
        held = await tx.get_pool_commitments(POOL, clock.now(), clock.now() + timedelta(hours=1))
    assert held == []


async def test_one_alert_while_work_is_in_recovery_and_one_end_when_none_is_left() -> None:
    uow, clock, booking_id = await _in_recovery()
    watch = _watch(uow, clock, set())

    assert await watch.check() == ["demo"]
    assert await watch.check() == []  # a steady state records nothing
    await queue_of(uow, clock).complete(ADMIN, "demo", booking_id)
    assert await watch.check() == ["demo"]

    assert _events(uow) == [
        (Outcome.FAILURE, RECOVERY_REASON, "count:1"),
        (Outcome.SUCCESS, "", "count:0"),
    ]


async def test_a_restart_closes_an_alert_whose_work_was_completed_meanwhile() -> None:
    uow, clock = world(), FixedClock(NOW)

    assert await _watch(uow, clock, {"demo"}).check() == ["demo"]
    assert await _watch(uow, clock, set()).check() == []

    assert _events(uow) == [(Outcome.SUCCESS, "", "count:0")]
