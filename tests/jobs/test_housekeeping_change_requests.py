"""Housekeeping deletes the bots' expired change requests once an hour (ADR 0023 step 2b)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.change_request_service import prune_change_requests
from app.domain.change_requests import ChangeRequest
from app.domain.storage import StorageLimits
from app.jobs.housekeeping import Housekeeping
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.fakes.storage import Disk
from tests.support.database import migrated_database
from tests.support.domain import NOW

KEEP = timedelta(days=30)


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "housekeeping-change-requests")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


def _request(public_id: str, expired_days_ago: int) -> ChangeRequest:
    expires = NOW - timedelta(days=expired_days_ago)
    return ChangeRequest(public_id, "opc_bot", 1, None, None, None, expires, expires)


async def test_expired_requests_go_once_an_hour_and_a_failure_never_stops_housekeeping(
    engine: AsyncEngine,
) -> None:
    clock = FixedClock(NOW)
    uow = InMemoryUnitOfWork(State())
    async with uow.locked() as tx:
        await tx.add_change_request(_request("chr_old", 31))
        await tx.add_change_request(_request("chr_new", 29))
    calls: list[int] = []

    async def prune() -> int:
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("database locked")
        return await prune_change_requests(uow, clock, KEEP)

    housekeeping = Housekeeping(engine, uow, clock, StorageLimits(), Disk(), prune_requests=prune)

    with capture_logs() as logs:
        await housekeeping.run()
        clock.advance(timedelta(minutes=59))
        await housekeeping.run()
        clock.advance(timedelta(minutes=1))
        await housekeeping.run()
        clock.advance(timedelta(hours=1))
        await housekeeping.run()

    assert len(calls) == 3
    assert [r.public_id for r in uow.state.change_requests.values()] == ["chr_new"]
    events = [(e["event"], e.get("requests")) for e in logs if "change_requests" in e["event"]]
    assert events == [
        ("change_requests.pruned", 1),
        ("change_requests.prune_failed", None),
    ]
