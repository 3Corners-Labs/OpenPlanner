"""Storage of updates (ADR 0029): the single release-check row and the request history."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.updates import (
    Release,
    ReleaseCheck,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
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
    for check in (ReleaseCheck(NOW, RELEASE), ReleaseCheck(NOW, None, "server_error")):
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
    )
    async with uow.locked() as tx:
        await tx.save_update_request(done)
    async with uow.read() as tx:
        assert await tx.get_update_requests() == [second, done]
        assert await tx.get_update_requests(limit=1) == [second]
