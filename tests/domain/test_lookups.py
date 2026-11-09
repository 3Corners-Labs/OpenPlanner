"""Group look-ups (ADR 0005): the one home for "the group with this slug, or a domain error"."""

from collections.abc import Awaitable, Callable

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import (
    group_booking,
    group_by_slug,
    group_in_organisation,
    group_summary_by_slug,
)
from app.domain.ports import Transaction
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations
from tests.support.closures import confirmed_booking


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations()
    state.bookings[10] = confirmed_booking(id=10, group_id=1)
    return InMemoryUnitOfWork(state)


async def test_group_by_slug_returns_the_group(uow: InMemoryUnitOfWork) -> None:
    async with uow.read() as tx:
        group = await group_by_slug(tx, "demo")
    assert (group.id, group.slug) == (1, "demo")


async def test_group_summary_by_slug_names_the_organisation(uow: InMemoryUnitOfWork) -> None:
    async with uow.read() as tx:
        summary = await group_summary_by_slug(tx, "andere-team")
    assert (summary.group.id, summary.organisation_slug) == (2, "andere")


@pytest.mark.parametrize("lookup", [group_by_slug, group_summary_by_slug])
async def test_an_unknown_group_is_not_found(
    uow: InMemoryUnitOfWork, lookup: Callable[[Transaction, str], Awaitable[object]]
) -> None:
    async with uow.read() as tx:
        with pytest.raises(DomainError) as caught:
            await lookup(tx, "nope")
    assert caught.value.code is ErrorCode.GROUP_NOT_FOUND


async def test_group_in_organisation_returns_the_group_of_that_organisation(
    uow: InMemoryUnitOfWork,
) -> None:
    async with uow.read() as tx:
        summary = await group_in_organisation(tx, "andere", "andere-team")
    assert (summary.group.id, summary.organisation_slug) == (2, "andere")


@pytest.mark.parametrize(
    ("organisation", "slug"),
    [("demo", "andere-team"), ("andere", "nope"), ("nope", "demo")],
    ids=["another organisation's group", "unknown group", "unknown organisation"],
)
async def test_group_in_organisation_answers_alike_for_what_is_not_its_group(
    uow: InMemoryUnitOfWork, organisation: str, slug: str
) -> None:
    async with uow.read() as tx:
        with pytest.raises(DomainError) as caught:
            await group_in_organisation(tx, organisation, slug)
    assert caught.value.code is ErrorCode.GROUP_NOT_FOUND


async def test_group_booking_returns_the_booking_and_its_group(uow: InMemoryUnitOfWork) -> None:
    async with uow.read() as tx:
        booking, group = await group_booking(tx, "demo", 10)
    assert (booking.id, group.slug) == (10, "demo")


@pytest.mark.parametrize(
    ("slug", "booking_id"),
    [("andere-team", 10), ("nope", 10), ("demo", 99)],
    ids=["another group's booking", "unknown group", "unknown booking"],
)
async def test_group_booking_answers_alike_for_what_is_not_the_groups(
    uow: InMemoryUnitOfWork, slug: str, booking_id: int
) -> None:
    async with uow.read() as tx:
        with pytest.raises(DomainError) as caught:
            await group_booking(tx, slug, booking_id)
    assert caught.value.code is ErrorCode.BOOKING_NOT_FOUND
