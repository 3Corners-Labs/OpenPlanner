"""Housekeeping removes previous webhook secrets past their overlap, hourly (ADR 0124 point 8)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.engine import create_engine
from app.domain.contact_center import ConnectionChange, ContactCenterKind
from app.domain.scheduling import Organisation
from app.domain.storage import StorageLimits
from app.domain.webhook_secret_sweep import PreviousSecretSweep
from app.jobs.housekeeping import SECRETS_EVERY, Housekeeping
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeContactCenterConnections
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.fakes.storage import Disk
from tests.support.database import migrated_database
from tests.support.domain import NOW

OVERLAP = timedelta(minutes=60)


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "housekeeping-secrets")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


class Dropper:
    """Counts how often housekeeping asked to remove previous secrets; fails on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return 1


def _housekeeping(engine: AsyncEngine, clock: FixedClock, dropper: Dropper) -> Housekeeping:
    return Housekeeping(
        engine,
        InMemoryUnitOfWork(State()),
        clock,
        StorageLimits(),
        Disk(),
        drop_secrets=dropper,
    )


async def test_previous_secrets_are_dropped_once_an_hour(engine: AsyncEngine) -> None:
    clock, dropper = FixedClock(NOW), Dropper()
    housekeeping = _housekeeping(engine, clock, dropper)

    with capture_logs() as logs:
        await housekeeping.run()
        clock.advance(SECRETS_EVERY - timedelta(minutes=1))
        await housekeeping.run()
        clock.advance(timedelta(minutes=1))
        await housekeeping.run()

    assert dropper.calls == 2
    assert [e["connections"] for e in logs if e["event"] == "webhook.previous_secrets_removed"] == [
        1,
        1,
    ]


async def test_a_failure_is_logged_by_its_type_only_and_the_loop_goes_on(
    engine: AsyncEngine,
) -> None:
    clock, dropper = FixedClock(NOW), Dropper()
    dropper.fail = True

    with capture_logs() as logs:
        await _housekeeping(engine, clock, dropper).run()

    failed = [e for e in logs if e["event"] == "webhook.previous_secrets_failed"]
    assert [e["error"] for e in failed] == ["RuntimeError"]
    assert "database locked" not in repr(logs)


async def test_the_sweep_lists_every_organisation_and_uses_the_live_overlap() -> None:
    state = State()
    clock = FixedClock(NOW)
    connections = FakeContactCenterConnections()
    sweep_overlap = [OVERLAP]
    sweep = PreviousSecretSweep(
        InMemoryUnitOfWork(state), clock, connections, lambda: sweep_overlap[0]
    )
    assert await sweep.run() == 0  # no organisation, nothing to do

    state.organisations |= {
        1: Organisation(1, "Demo", "demo"),
        2: Organisation(2, "Andere", "andere"),
    }
    for organisation_id in (1, 2):
        await connections.save(
            organisation_id,
            ConnectionChange(ContactCenterKind.WEBEX_CC, "https://api.wxcc-eu1.cisco.com", "c"),
            NOW,
        )
        await connections.rotate_webhook_secret(organisation_id, NOW)

    inside = await sweep.run()
    clock.advance(timedelta(minutes=30))
    sweep_overlap[0] = timedelta(minutes=10)  # the setting was lowered in the meantime
    shortened = await sweep.run()

    assert (inside, shortened) == (0, 2)


async def test_without_a_settings_key_the_sweep_does_nothing() -> None:
    sweep = PreviousSecretSweep(InMemoryUnitOfWork(State()), FixedClock(NOW), None, lambda: OVERLAP)

    assert await sweep.run() == 0
