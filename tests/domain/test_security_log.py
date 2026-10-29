"""Security events (ADR 0034, AU-4): global, category security, one per kind per window."""

from datetime import timedelta

from app.domain.audit import Category, Level, Outcome, Source
from app.domain.security_log import INVALID_SIGNATURE, WEBHOOK_REJECTED, SecurityLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

WINDOW = timedelta(minutes=15)


def _log() -> tuple[SecurityLog, InMemoryUnitOfWork, FixedClock]:
    uow, clock = InMemoryUnitOfWork(State()), FixedClock(NOW)
    return SecurityLog(uow, clock, WINDOW), uow, clock


async def _reject(log: SecurityLog, source: str = "webex") -> bool:
    return await log.record(
        WEBHOOK_REJECTED, Outcome.DENIED, reason=INVALID_SIGNATURE, detail=f"source:{source}"
    )


async def test_a_rejection_is_a_global_security_event() -> None:
    log, uow, _ = _log()

    assert await _reject(log)

    (event,) = uow.state.audit
    assert (event.organisation, event.group, event.source) == (None, None, Source.SYSTEM)
    assert (event.category, event.level) == (Category.SECURITY, Level.WARNING)
    assert (event.action, event.reason, event.detail) == (
        WEBHOOK_REJECTED,
        INVALID_SIGNATURE,
        "source:webex",
    )


async def test_a_flood_gives_one_event_per_window_and_counts_the_rest() -> None:
    log, uow, clock = _log()

    written = [await _reject(log) for _ in range(500)]
    clock.advance(WINDOW - timedelta(seconds=1))
    written.append(await _reject(log))
    clock.advance(timedelta(seconds=1))
    written.append(await _reject(log))

    assert written.count(True) == 2
    assert [e.detail for e in uow.state.audit] == ["source:webex", "source:webex,suppressed:500"]


async def test_each_kind_has_its_own_window() -> None:
    log, uow, _ = _log()

    await _reject(log, "webex")
    await _reject(log, "wxcc")
    await log.record("webex.token_refresh", Outcome.FAILURE, reason="unauthorized")

    assert len(uow.state.audit) == 3
    assert uow.state.audit[2].level is Level.ERROR
