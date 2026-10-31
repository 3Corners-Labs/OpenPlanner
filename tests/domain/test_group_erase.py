"""Archived groups are erased once they have waited and nothing refers to them (ADR 0084)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.booking import Slot
from app.domain.group_erase import ERASED, GroupEraser
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations
from tests.support.domain import NOW, SLOT_0900

AFTER = timedelta(days=30)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _eraser(uow: InMemoryUnitOfWork, clock: FixedClock) -> GroupEraser:
    return GroupEraser(uow, clock, lambda: AFTER)


async def test_a_group_is_erased_after_the_delay_with_a_system_event(
    uow: InMemoryUnitOfWork,
) -> None:
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    clock = FixedClock(NOW + AFTER - timedelta(minutes=1))

    assert await _eraser(uow, clock).run() == 0
    assert 1 in uow.state.groups

    clock.advance(timedelta(minutes=1))
    assert await _eraser(uow, clock).run() == 1
    assert 1 not in uow.state.groups
    assert 2 in uow.state.groups  # in use, never touched
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome, event.source) == (
        SYSTEM_ACTOR,
        ERASED,
        Outcome.SUCCESS,
        Source.SYSTEM,
    )
    assert (event.organisation, event.group) == ("demo", "demo")


async def test_a_referenced_group_waits(uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    uow.state.slots[50] = Slot(50, 1, SLOT_0900, SLOT_0900 + timedelta(minutes=30), 2)
    clock = FixedClock(NOW + AFTER)

    assert await _eraser(uow, clock).run() == 0
    assert 1 in uow.state.groups

    del uow.state.slots[50]
    assert await _eraser(uow, clock).run() == 1


async def test_a_group_in_use_is_never_erased(uow: InMemoryUnitOfWork) -> None:
    assert await _eraser(uow, FixedClock(NOW + timedelta(days=400))).run() == 0
    assert set(uow.state.groups) == {1, 2}
