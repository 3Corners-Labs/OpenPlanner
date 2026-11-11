"""The worker's repair of the legacy pools and flows at start (ADR 0121 step 2e)."""

from typing import NoReturn

import pytest
from structlog.testing import capture_logs

from app.jobs.flows import repair_default_flows
from tests.fakes.persistence import InMemoryTransaction, InMemoryUnitOfWork
from tests.support.admin import two_organisations


class BrokenUow(InMemoryUnitOfWork):
    """A unit of work whose every transaction fails, as before the schema is migrated."""

    def read(self) -> NoReturn:
        """Fail like a database without tables."""
        raise RuntimeError("secret detail")


async def test_the_repair_logs_how_many_groups_it_gave_rows_and_nothing_else() -> None:
    uow = InMemoryUnitOfWork(two_organisations())

    with capture_logs() as logs:
        await repair_default_flows(uow)
        await repair_default_flows(uow)

    assert logs == [{"event": "flows.repaired", "log_level": "info", "groups": 2}]
    assert len(uow.state.pools) == 2


async def test_a_failing_repair_is_logged_with_the_error_type_and_never_stops_the_start() -> None:
    with capture_logs() as logs:
        await repair_default_flows(BrokenUow())

    assert logs == [
        {"event": "flows.repair_failed", "log_level": "warning", "error": "RuntimeError"}
    ]


async def test_a_group_that_cannot_be_repaired_is_counted_and_the_others_are_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    real = InMemoryTransaction.ensure_default_flows

    async def refuse_group_one(self: InMemoryTransaction, group_id: int) -> bool:
        if group_id == min(uow.state.groups):
            raise RuntimeError("secret detail")
        return await real(self, group_id)

    monkeypatch.setattr(InMemoryTransaction, "ensure_default_flows", refuse_group_one)

    with capture_logs() as logs:
        await repair_default_flows(uow)

    assert logs == [
        {"event": "flows.repaired", "log_level": "info", "groups": 1},
        {
            "event": "flows.repair_incomplete",
            "log_level": "warning",
            "groups": 1,
            "error": "RuntimeError",
        },
    ]
