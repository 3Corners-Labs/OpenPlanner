"""Storage of updates (ADR 0029): the single release-check row and the request history."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.db.updates import last_running_version
from app.domain.updates import (
    Release,
    ReleaseCheck,
    RunningVersion,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
    UpdateStep,
    Version,
)
from tests.support.domain import NOW

RELEASE = Release(Version(1, 4, 0), "https://github.com/acme/openplanner/releases/tag/v1.4.0")


@pytest.fixture
def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


async def test_release_check_round_trip_keeps_one_row(uow: SqlUnitOfWork) -> None:
    async with uow.read() as tx:
        assert await tx.get_release_check() is None
    checks = (
        ReleaseCheck(NOW, RELEASE),
        ReleaseCheck(NOW, None, "server_error"),
        ReleaseCheck(NOW, RELEASE, requested_at=NOW + timedelta(minutes=5)),
        ReleaseCheck(NOW + timedelta(minutes=6), RELEASE),
    )
    for check in checks:
        async with uow.locked() as tx:
            await tx.save_release_check(check)
        async with uow.read() as tx:
            assert await tx.get_release_check() == check


async def test_update_requests_newest_first_and_saved_changes(uow: SqlUnitOfWork) -> None:
    first = UpdateRequest(
        id=None,
        action=UpdateAction.UPDATE,
        target=Version(1, 4, 0),
        from_version=Version(1, 3, 0),
        requested_by="s-1",
        requested_at=NOW,
        status=UpdateStatus.REQUESTED,
    )
    async with uow.locked() as tx:
        stored = await tx.add_update_request(first)
        second = await tx.add_update_request(
            replace(first, action=UpdateAction.RESTORE, requested_at=NOW + timedelta(hours=1))
        )
    assert stored == replace(first, id=stored.id)
    done = replace(
        stored,
        status=UpdateStatus.ROLLED_BACK,
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=3),
        reason="health_check_failed",
        step=UpdateStep.ROLLING_BACK,
        step_at=NOW + timedelta(minutes=2),
    )
    async with uow.locked() as tx:
        await tx.save_update_request(done)
    async with uow.read() as tx:
        assert await tx.get_update_requests() == [second, done]
        assert await tx.get_update_requests(limit=1) == [second]


# ADR 0116: apply_after, the support index, and the version that last ran.


async def test_apply_after_and_the_support_index_round_trip(uow: SqlUnitOfWork) -> None:
    later = NOW + timedelta(hours=3)
    async with uow.locked() as tx:
        stored = await tx.add_update_request(
            UpdateRequest(
                None,
                UpdateAction.UPDATE,
                Version(1, 4, 0),
                Version(1, 3, 0),
                "s-1",
                NOW,
                UpdateStatus.REQUESTED,
                apply_after=later,
            )
        )
        await tx.save_release_check(ReleaseCheck(NOW, None, support='{"lines": []}'))
    async with uow.read() as tx:
        (found,) = await tx.get_update_requests()
        check = await tx.get_release_check()
    assert found.apply_after == later == stored.apply_after
    assert check is not None
    assert check.support == '{"lines": []}'


async def test_the_last_running_version_is_none_for_a_new_database(
    uow: SqlUnitOfWork, engine: AsyncEngine, tmp_path: Path
) -> None:
    assert await last_running_version(engine) is None
    async with uow.locked() as tx:
        await tx.add_running_version(RunningVersion("0.9.4", "", NOW, None))
        await tx.add_running_version(RunningVersion("1.0.1", "", NOW, None))
    assert await last_running_version(engine) == "1.0.1"

    empty = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    try:
        assert await last_running_version(empty) is None
    finally:
        await empty.dispose()
