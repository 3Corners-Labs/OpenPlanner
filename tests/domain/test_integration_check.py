"""IntegrationCheckService: the "Koppeling controleren" button per group."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal
from app.domain.checks import CheckArea, CheckStatus, Finding, GroupCheck
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration_check import IntegrationCheckService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeChecker
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, two_organisations
from tests.support.domain import NOW, make_group

OK = Finding(CheckArea.WEBEX, "webex.host_ok", CheckStatus.OK, (("host", "h@example.nl"),))
WARN = Finding(CheckArea.WXCC, "wxcc.screenpop.missing", CheckStatus.WARN)
FAIL = Finding(CheckArea.WXCC, "wxcc.schema.refused", CheckStatus.FAIL, (("missing", "kind"),))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations()
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-2", name="Demo 2")
    return InMemoryUnitOfWork(state)


def _service(uow: InMemoryUnitOfWork, checker: FakeChecker | None) -> IntegrationCheckService:
    clock = FixedClock(NOW)
    return IntegrationCheckService(uow, clock, AdminService(uow, clock), checker)


def test_overall_status_is_the_worst_finding() -> None:
    assert GroupCheck(1, NOW, (OK, WARN, FAIL)).overall is CheckStatus.FAIL
    assert GroupCheck(1, NOW, (OK, WARN)).overall is CheckStatus.WARN
    assert GroupCheck(1, NOW, (OK,)).overall is CheckStatus.OK
    skipped = Finding(CheckArea.WEBEX, "webex.not_configured", CheckStatus.SKIPPED)
    assert GroupCheck(1, NOW, (skipped,)).overall is CheckStatus.SKIPPED
    assert GroupCheck(1, NOW, ()).overall is CheckStatus.SKIPPED


async def test_run_stores_the_result_and_audits_it(uow: InMemoryUnitOfWork) -> None:
    checker = FakeChecker([OK, WARN, FAIL])
    service = _service(uow, checker)

    check = await service.run(ORG_ADMIN, "demo")

    assert check.findings == (OK, WARN, FAIL)
    assert check.checked_at == NOW
    # Peers are the groups of the same organisation only: no other organisation's slugs.
    assert checker.calls == [("demo", ["demo", "demo-2"])]
    assert await service.last(ORG_ADMIN, "demo") == check
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.reason, event.group) == (
        "integration.check",
        Outcome.FAILURE,
        "fail",
        "demo",
    )


async def test_a_clean_check_is_audited_as_success(uow: InMemoryUnitOfWork) -> None:
    await _service(uow, FakeChecker([OK])).run(ORG_ADMIN, "demo")
    assert (uow.state.audit[-1].outcome, uow.state.audit[-1].reason) == (Outcome.SUCCESS, "ok")


async def test_without_platforms_the_check_says_so(uow: InMemoryUnitOfWork) -> None:
    check = await _service(uow, None).run(ORG_ADMIN, "demo")

    assert [f.code for f in check.findings] == ["platform.not_configured"]
    assert check.overall is CheckStatus.SKIPPED


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_only_those_who_edit_the_group_may_check_it(
    uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    service = _service(uow, FakeChecker([OK]))

    for call in (service.run, service.last):
        with pytest.raises(DomainError) as error:
            await call(principal, "demo")
        assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_no_result_before_the_first_check(uow: InMemoryUnitOfWork) -> None:
    assert await _service(uow, FakeChecker([OK])).last(ORG_ADMIN, "demo") is None


async def test_a_new_check_replaces_the_old_one(uow: InMemoryUnitOfWork) -> None:
    await _service(uow, FakeChecker([FAIL])).run(ORG_ADMIN, "demo")
    service = _service(uow, FakeChecker([OK]))  # as after fixing Control Hub

    await service.run(ORG_ADMIN, "demo")

    last = await service.last(ORG_ADMIN, "demo")
    assert last is not None
    assert last.findings == (OK,)
