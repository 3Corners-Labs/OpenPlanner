"""An admin's own overview layout (ADR 0096 step 2): the rules and the service."""

import pytest

from app.domain.authorization import Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.overview_layout import (
    OverviewLayoutService,
    add,
    addable,
    effective,
    move,
    remove,
)
from app.domain.staff import StaffMember
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

ALLOWED = ("alerts", "queue", "bookings-today", "rights")
DEFAULT = ("alerts", "status", "queue")


def _field(error: pytest.ExceptionInfo[DomainError]) -> str | None:
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    return error.value.field


def test_without_an_own_layout_the_default_shows_what_the_reader_may_see() -> None:
    assert effective(None, DEFAULT, ALLOWED) == ("alerts", "queue")  # status: no right


def test_an_own_layout_skips_widgets_whose_right_was_lost() -> None:
    assert effective(("rights", "status", "queue"), DEFAULT, ALLOWED) == ("rights", "queue")
    assert effective((), DEFAULT, ALLOWED) == ()  # an empty overview is the admin's choice


def test_only_allowed_widgets_not_shown_yet_can_be_added() -> None:
    assert addable(("queue",), ALLOWED) == ("alerts", "bookings-today", "rights")
    assert add(("queue",), "rights", ALLOWED, 4) == ("queue", "rights")
    with pytest.raises(DomainError) as shown:
        add(("queue",), "queue", ALLOWED, 4)
    with pytest.raises(DomainError) as forbidden:
        add(("queue",), "status", ALLOWED, 4)
    with pytest.raises(DomainError) as full:
        add(("queue", "alerts"), "rights", ALLOWED, 2)

    assert _field(shown) == _field(forbidden) == "widget"
    assert _field(full) == "widget_count"


def test_remove_and_move_change_only_the_layout() -> None:
    layout = ("alerts", "queue", "rights")

    assert remove(layout, "queue") == ("alerts", "rights")
    assert move(layout, "queue", -1) == ("queue", "alerts", "rights")
    assert move(layout, "queue", 1) == ("alerts", "rights", "queue")
    assert move(layout, "alerts", -1) == layout  # already first
    assert move(layout, "rights", 1) == layout  # already last
    with pytest.raises(DomainError) as missing:
        remove(layout, "status")
    with pytest.raises(DomainError) as jump:
        move(layout, "queue", 2)
    with pytest.raises(DomainError) as absent:
        move(layout, "status", 1)

    assert _field(missing) == _field(absent) == "widget"
    assert _field(jump) == "offset"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.staff["sub-gina"] = StaffMember(
        "sub-gina", "Gina", frozenset({Role.ORG_ADMIN}), frozenset(), NOW, NOW, NOW
    )
    return InMemoryUnitOfWork(state)


KNOWN = ("alerts", "queue", "bookings-today", "rights", "status")
SEEN = ("alerts", "queue", "rights")
"""What Gina's rights let her see."""


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> OverviewLayoutService:
    return OverviewLayoutService(uow, FixedClock(NOW), KNOWN, lambda: 3)


async def test_each_step_starts_from_what_the_overview_shows_and_is_kept(
    service: OverviewLayoutService,
) -> None:
    default = ("alerts", "status", "queue")
    assert await service.layout("sub-gina", default, SEEN) == ("alerts", "queue")
    assert not await service.is_own("sub-gina")

    await service.move("sub-gina", "queue", -1, default, SEEN)
    assert await service.layout("sub-gina", default, SEEN) == ("queue", "alerts")
    await service.add("sub-gina", "rights", default, SEEN)
    await service.remove("sub-gina", "alerts", default, SEEN)
    assert await service.layout("sub-gina", default, SEEN) == ("queue", "rights")
    assert await service.is_own("sub-gina")

    await service.reset("sub-gina")
    assert await service.layout("sub-gina", default, SEEN) == ("alerts", "queue")
    assert service.maximum == 3


async def test_the_maximum_stops_adding_but_never_shortening(
    service: OverviewLayoutService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.overview_layouts["sub-gina"] = ("alerts", "queue", "rights")  # at the maximum

    with pytest.raises(DomainError) as full:
        await service.add("sub-gina", "status", (), KNOWN)
    uow.state.overview_layouts["sub-gina"] = KNOWN  # above a lowered maximum
    await service.move("sub-gina", "status", -1, (), KNOWN)
    await service.remove("sub-gina", "alerts", (), KNOWN)

    assert _field(full) == "widget_count"
    assert uow.state.overview_layouts["sub-gina"] == ("queue", "bookings-today", "status", "rights")


async def test_a_widget_outside_the_register_is_never_stored(
    uow: InMemoryUnitOfWork,
) -> None:
    service = OverviewLayoutService(uow, FixedClock(NOW), ("queue",), lambda: 5)

    with pytest.raises(DomainError) as refused:
        await service.add("sub-gina", "secret-widget", (), ("queue", "secret-widget"))

    assert _field(refused) == "widget"
    assert "sub-gina" not in uow.state.overview_layouts


async def test_a_person_without_a_staff_record_has_no_layout(
    service: OverviewLayoutService,
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.add("sub-unknown", "queue", (), SEEN)

    assert _field(refused) == "staff"


async def test_erasing_the_person_removes_the_layout(
    service: OverviewLayoutService, uow: InMemoryUnitOfWork
) -> None:
    await service.add("sub-gina", "queue", (), SEEN)
    async with uow.locked() as tx:
        await tx.erase_staff("sub-gina", NOW)

    assert not await service.is_own("sub-gina")
