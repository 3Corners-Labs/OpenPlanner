import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.checks import CheckArea, CheckStatus, Finding
from app.domain.maintenance import MaintenanceState
from app.domain.updates import Version
from app.jobs.healthcheck import heartbeat_is_fresh
from app.jobs.worker import run_worker
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeChecker
from tests.support.database import migrated_database

DEADLINE = 15.0
"""Seconds a worker may take for its first round; generous, since tests run in parallel."""


async def _until(condition: Callable[[], bool]) -> None:
    """Wait until ``condition`` holds, failing after ``DEADLINE`` seconds."""
    async with asyncio.timeout(DEADLINE):
        while not condition():  # noqa: ASYNC110 - polls a file the worker writes
            await asyncio.sleep(0.01)


async def test_worker_writes_heartbeat_and_stops_on_event(settings: Settings) -> None:
    stop = asyncio.Event()
    clock = FixedClock()
    task = asyncio.create_task(run_worker(settings, stop, clock=clock))

    await _until(settings.worker_heartbeat_path.exists)
    stop.set()
    await asyncio.wait_for(task, timeout=DEADLINE)

    assert settings.worker_heartbeat_path.read_text() == clock.now().isoformat()


async def test_worker_skips_heartbeat_when_database_is_down(
    unreachable_db_settings: Settings,
) -> None:
    broken = unreachable_db_settings
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(broken, stop, clock=FixedClock()))

    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=DEADLINE)

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


@pytest.fixture
def migrated_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "releases")


async def test_worker_looks_up_the_latest_release_once_per_hour(
    settings: Settings, migrated_url: str
) -> None:
    url = migrated_url
    configured = settings.model_copy(
        update={"database_url": url, "update_repository": "acme/openplanner"}
    )
    calls: list[str] = []

    def github(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        page = "https://github.com/acme/openplanner/releases/tag/v1.4.0"
        return httpx.Response(200, json={"tag_name": "v1.4.0", "html_url": page})

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(configured, stop, clock=FixedClock(), transport=httpx.MockTransport(github))
    )
    await _until(configured.worker_heartbeat_path.exists)
    await asyncio.sleep(0.05)  # a few more iterations: the clock stands still, so no new check
    stop.set()
    await asyncio.wait_for(task, timeout=DEADLINE)

    assert calls == ["/repos/acme/openplanner/releases/latest"]
    engine = create_engine(url)
    try:
        async with SqlUnitOfWork(engine).read() as tx:
            check = await tx.get_release_check()
    finally:
        await engine.dispose()
    assert check is not None
    assert check.latest is not None
    assert check.latest.version == Version(1, 4, 0)


async def test_worker_reports_maintenance_that_has_been_on_too_long(
    settings: Settings, migrated_url: str
) -> None:
    # ADR 0038 §6: no automatic end; the worker records a system event after the threshold.
    url = migrated_url
    configured = settings.model_copy(update={"database_url": url})
    clock = FixedClock()
    engine = create_engine(url)
    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.save_maintenance_state(
                MaintenanceState(True, clock.now() - timedelta(hours=5), "host-cli")
            )
    finally:
        await engine.dispose()

    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(configured, stop, clock=clock))
    for _ in range(200):
        if configured.worker_heartbeat_path.exists():
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # more iterations: reported once, not on every loop
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    engine = create_engine(url)
    try:
        async with SqlUnitOfWork(engine).read() as tx:
            events = await tx.list_audit_events(Scope(everything=True), 50)
    finally:
        await engine.dispose()
    assert [e.event.detail for e in events if e.event.action == "maintenance.overdue"] == [
        "hours:5"
    ]


REFUSED = Finding(
    CheckArea.WEBEX,
    "webex.schedule_refused",
    CheckStatus.FAIL,
    (("host", "host@example.nl"), ("error", "rejected_400"), ("reason", "no licence")),
)


async def test_worker_checks_every_group_with_a_host_once_per_interval(
    settings: Settings, migrated_url: str
) -> None:
    # ADR 0065 step 2: the sweep checks groups with a Webex host and reports a broken chain
    # as a system event at the group; a group without a host is left alone.
    url = migrated_url
    configured = settings.model_copy(update={"database_url": url})
    clock = FixedClock()
    engine = create_engine(url)
    try:
        await seed_demo(engine)
        async with SqlUnitOfWork(engine).locked() as tx:
            demo = await tx.get_group("demo")
            assert demo is not None
            await tx.save_group(replace(demo, video_host_email="host@example.nl"))
    finally:
        await engine.dispose()
    checker = FakeChecker([REFUSED])

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(configured, stop, clock=clock, integrations=Integrations(checker=checker))
    )
    await _until(configured.worker_heartbeat_path.exists)
    await asyncio.sleep(0.05)  # more iterations: the clock stands still, so no second check
    stop.set()
    await asyncio.wait_for(task, timeout=DEADLINE)

    assert [slug for slug, _ in checker.calls] == ["demo"]
    engine = create_engine(url)
    try:
        async with SqlUnitOfWork(engine).read() as tx:
            check = await tx.get_group_check(demo.id)
            events = await tx.list_audit_events(Scope(everything=True), 50)
    finally:
        await engine.dispose()
    assert check is not None
    assert [f for f in check.findings if f.area is not CheckArea.WORKER] == [REFUSED]
    checks = [e.event for e in events if e.event.action == "integration.check"]
    assert [(e.actor, e.outcome, e.reason, e.message, e.organisation, e.group) for e in checks] == [
        ("system", Outcome.FAILURE, "webex.schedule_refused", "no licence", "demo", "demo")
    ]
