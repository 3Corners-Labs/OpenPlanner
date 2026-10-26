import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.jobs.healthcheck import heartbeat_is_fresh
from app.jobs.worker import run_worker
from tests.fakes.clock import FixedClock


async def test_worker_writes_heartbeat_and_stops_on_event(settings: Settings) -> None:
    stop = asyncio.Event()
    clock = FixedClock()
    task = asyncio.create_task(run_worker(settings, stop, clock=clock))

    for _ in range(200):
        if settings.worker_heartbeat_path.exists():
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert settings.worker_heartbeat_path.read_text() == clock.now().isoformat()


async def test_worker_skips_heartbeat_when_database_is_down(
    unreachable_db_settings: Settings,
) -> None:
    broken = unreachable_db_settings
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(broken, stop, clock=FixedClock()))

    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert not broken.worker_heartbeat_path.exists()


def test_heartbeat_fresh_within_max_age(tmp_path: Path) -> None:
    clock = FixedClock()
    path = tmp_path / "hb"
    path.write_text(clock.now().isoformat())
    clock.advance(timedelta(seconds=59))

    assert heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=clock.now())


def test_heartbeat_stale_after_max_age(tmp_path: Path) -> None:
    clock = FixedClock()
    path = tmp_path / "hb"
    path.write_text(clock.now().isoformat())
    clock.advance(timedelta(seconds=61))

    assert not heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=clock.now())


@pytest.mark.parametrize("content", [None, "not a timestamp", "2026-01-05T09:00:00"])
def test_heartbeat_missing_or_invalid_is_not_fresh(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "hb"
    if content is not None:
        path.write_text(content)

    assert not heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=FixedClock().now())
