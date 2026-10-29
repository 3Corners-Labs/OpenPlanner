"""Security events (ADR 0034, AU-4): global, category security, one per kind per window."""

from datetime import timedelta

from app.domain.audit import Category, Level, Outcome, Source
from app.domain.client_pseudonym import MANY_CLIENTS
from app.domain.security_log import (
    INVALID_SIGNATURE,
    RATE_LIMITED,
    TOO_MANY_REQUESTS,
    WEBHOOK_REJECTED,
    SecurityLog,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

WINDOW = timedelta(minutes=15)


def _log(max_sources: int = 1000) -> tuple[SecurityLog, InMemoryUnitOfWork, FixedClock]:
    uow, clock = InMemoryUnitOfWork(State()), FixedClock(NOW)
    return SecurityLog(uow, clock, WINDOW, max_sources=max_sources), uow, clock


async def _flood(log: SecurityLog, client: str) -> bool:
    return await log.record(
        RATE_LIMITED, Outcome.DENIED, reason=TOO_MANY_REQUESTS, detail="surface:web", client=client
    )


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


async def test_the_client_is_part_of_the_kind_and_the_detail() -> None:
    log, uow, _ = _log()

    assert await _flood(log, "aaaa")
    assert not await _flood(log, "aaaa")
    assert await _flood(log, "bbbb")

    assert [e.detail for e in uow.state.audit] == [
        "surface:web,client:aaaa",
        "surface:web,client:bbbb",
    ]


async def test_an_actor_can_be_given_for_a_visitor() -> None:
    log, uow, _ = _log()

    await log.record("admin.login", Outcome.DENIED, reason="invalid_state", actor="anonymous")

    (event,) = uow.state.audit
    assert (event.actor, event.category) == ("anonymous", Category.SECURITY)


async def test_beyond_the_bound_further_clients_count_as_many() -> None:
    log, uow, _ = _log(max_sources=2)

    for client in ("c1", "c2", "c3", "c4"):
        await _flood(log, client)

    details = [e.detail for e in uow.state.audit]
    assert details == [
        "surface:web,client:c1",
        "surface:web,client:c2",
        f"surface:web,client:{MANY_CLIENTS}",
    ]


async def test_expired_windows_make_room_for_new_clients() -> None:
    log, uow, clock = _log(max_sources=2)
    await _flood(log, "c1")
    await _flood(log, "c2")

    clock.advance(WINDOW)
    await _flood(log, "c3")

    assert uow.state.audit[-1].detail == "surface:web,client:c3"
