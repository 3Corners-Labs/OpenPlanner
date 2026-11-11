"""The repair walk of the legacy pools and flows (ADR 0121 step 2e)."""

import pytest

from app.domain.default_flows_service import RepairCounts, ensure_all_default_flows
from tests.fakes.persistence import InMemoryTransaction, InMemoryUnitOfWork
from tests.support.admin import two_organisations
from tests.support.flow_backends import CountingUow


async def test_one_group_that_fails_does_not_stop_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    async with uow.read() as tx:
        first, second = await tx.groups_missing_default_flows()
    real = InMemoryTransaction.ensure_default_flows

    async def refuse_the_first(self: InMemoryTransaction, group_id: int) -> bool:
        if group_id == first:
            raise RuntimeError("secret detail")
        return await real(self, group_id)

    monkeypatch.setattr(InMemoryTransaction, "ensure_default_flows", refuse_the_first)

    assert await ensure_all_default_flows(uow) == RepairCounts(1, 1)

    async with uow.read() as tx:
        assert await tx.groups_missing_default_flows() == [first]  # left for the next run
    assert second in {p.group_id for p in uow.state.pools.values()}


async def test_nothing_missing_opens_no_write_transaction() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    assert await ensure_all_default_flows(uow) == RepairCounts(2, 0)
    counting = CountingUow(uow)

    assert await ensure_all_default_flows(counting) == RepairCounts(0, 0)

    assert counting.writes == 0
