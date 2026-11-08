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


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> OverviewLayoutService:
    return OverviewLayoutService(uow, FixedClock(NOW), ("alerts", "queue", "rights"), lambda: 2)


async def test_the_service_saves_and_resets_the_own_layout(service: OverviewLayoutService) -> None:
    assert await service.stored("sub-gina") is None

    await service.save("sub-gina", ["queue", "alerts"])
    assert await service.stored("sub-gina") == ("queue", "alerts")

    await service.reset("sub-gina")
    assert await service.stored("sub-gina") is None
    assert service.maximum == 2


@pytest.mark.parametrize(
    ("keys", "field"),
    [
        (["queue", "queue"], "widget"),
        (["queue", "secret-widget"], "widget"),
        (["queue", "alerts", "rights"], "widget_count"),
    ],
)
async def test_the_service_stores_only_known_keys_once_and_within_the_maximum(
    service: OverviewLayoutService, keys: list[str], field: str
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save("sub-gina", keys)

    assert _field(refused) == field
    assert await service.stored("sub-gina") is None


async def test_a_person_without_a_staff_record_has_no_layout(
    service: OverviewLayoutService,
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save("sub-unknown", ["queue"])

    assert _field(refused) == "staff"
