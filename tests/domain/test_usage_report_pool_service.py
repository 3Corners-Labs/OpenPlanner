"""Who reads the pools' utilisation, and over which days (ADR 0122 step 5; G41)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role, Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage_report_pool_service import PoolUsageService
from app.domain.usage_report_pools import PoolInput, PoolInputs
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW
from tests.support.principals import principal

ORG_ADMIN = principal("o", "O", [Role.ORG_ADMIN], organisations=["demo"])
GROUP_ADMIN = principal("g", "G", [Role.GROUP_ADMIN], groups=["balie"])
AGENT = principal("a", "A", [Role.AGENT], groups=["balie"])
RETENTION = 365


class PoolStore:
    """A store that remembers what it was asked for and returns one empty pool."""

    def __init__(self) -> None:
        self.asked: list[tuple[Scope, str, date, date]] = []

    async def read_pool_report_inputs(
        self, scope: Scope, organisation_slug: str, first_day: date, last_day: date
    ) -> PoolInputs | None:
        self.asked.append((scope, organisation_slug, first_day, last_day))
        return PoolInputs(1, (PoolInput(1, "Loket", False, None, frozenset(), ()),))


class PoolUow:
    def __init__(self, store: PoolStore) -> None:
        self.store = store

    @asynccontextmanager
    async def read(self) -> AsyncIterator[Any]:
        yield self.store


@pytest.fixture
def store() -> PoolStore:
    return PoolStore()


@pytest.fixture
def service(store: PoolStore, clock: FixedClock) -> PoolUsageService:
    admin = AdminService(InMemoryUnitOfWork(), clock)
    return PoolUsageService(
        PoolUow(store), admin, clock, lambda: 5, lambda _org: 5, lambda: RETENTION
    )  # type: ignore[arg-type]


async def test_an_organisation_admin_reads_the_pools_with_the_organisations_scope(
    service: PoolUsageService, store: PoolStore
) -> None:
    day = NOW.date()

    report = await service.report(ORG_ADMIN, "demo", day, day)

    assert report is not None
    assert [p.name for p in report.pools] == ["Loket"]
    scope, slug, first, last = store.asked[0]
    assert (slug, first, last) == ("demo", day, day)
    assert "demo" in scope.organisations


@pytest.mark.parametrize("who", [GROUP_ADMIN, AGENT])
async def test_a_reader_of_one_service_never_reads_the_pools(
    service: PoolUsageService, store: PoolStore, who: Any
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.report(who, "demo", NOW.date(), NOW.date())

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert store.asked == []


async def test_a_reader_of_another_organisation_is_refused_and_audited(
    service: PoolUsageService, store: PoolStore
) -> None:
    other = principal("x", "X", [Role.ORG_ADMIN], organisations=["andere"])

    with pytest.raises(DomainError) as refused:
        await service.report(other, "demo", NOW.date(), NOW.date())

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert store.asked == []
    assert Outcome.DENIED in {event.outcome for event in service._admin._uow.state.audit}  # noqa: SLF001


async def test_the_range_starts_no_earlier_than_the_rows_are_kept(
    service: PoolUsageService, store: PoolStore
) -> None:
    today = NOW.date()

    report = await service.report(ORG_ADMIN, "demo", today - timedelta(days=399), today)

    assert report is not None
    assert report.first_day == today - timedelta(days=RETENTION)
    assert store.asked[0][2] == report.first_day


async def test_a_backwards_or_too_wide_range_is_refused(service: PoolUsageService) -> None:
    today = NOW.date()

    for first, last in ((today, today - timedelta(days=1)), (today - timedelta(days=400), today)):
        with pytest.raises(DomainError) as refused:
            await service.report(ORG_ADMIN, "demo", first, last)
        assert refused.value.code is ErrorCode.INVALID_RANGE
