"""The worker's repair of the legacy pools and flows at start (ADR 0121 step 2e)."""

from structlog.testing import capture_logs

from app.jobs.flows import repair_default_flows
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations


async def test_the_repair_logs_how_many_groups_it_gave_rows_and_nothing_else() -> None:
    uow = InMemoryUnitOfWork(two_organisations())

    with capture_logs() as logs:
        await repair_default_flows(uow)
        await repair_default_flows(uow)

    assert logs == [{"event": "flows.repaired", "log_level": "info", "groups": 2}]
    assert len(uow.state.pools) == 2


async def test_a_failing_repair_is_logged_with_the_error_type_and_never_stops_the_start() -> None:
    class Broken:
        def locked(self) -> None:
            raise RuntimeError("secret detail")

    with capture_logs() as logs:
        await repair_default_flows(Broken())  # type: ignore[arg-type]

    assert logs == [
        {"event": "flows.repair_failed", "log_level": "warning", "error": "RuntimeError"}
    ]
