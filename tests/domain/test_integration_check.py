"""IntegrationCheckService: the "Koppeling controleren" button per group and the automatic check."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.authorization import Principal
from app.domain.checks import CheckArea, CheckStatus, Finding, GroupCheck
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration_check import (
    LOOK_EVERY,
    CheckReport,
    IntegrationCheckService,
    IntegrationWatch,
    check_due,
    report_for,
)
from app.domain.job_plan import JobType
from app.domain.system_log import SystemLog
from app.domain.worker_check import WorkerHeartbeat
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeChecker
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, two_organisations
from tests.support.domain import NOW, make_group

OK = Finding(CheckArea.WEBEX, "webex.host_ok", CheckStatus.OK, (("host", "h@example.nl"),))
WARN = Finding(CheckArea.WXCC, "wxcc.screenpop.missing", CheckStatus.WARN)
FAIL = Finding(CheckArea.WXCC, "wxcc.schema.refused", CheckStatus.FAIL, (("missing", "kind"),))
REFUSED = Finding(
    CheckArea.WEBEX,
    "webex.schedule_refused",
    CheckStatus.FAIL,
    (("host", "h@example.nl"), ("error", "rejected_400"), ("reason", "timezone does not match")),
)
UNEXPLAINED = Finding(
    CheckArea.WEBEX, "webex.unreachable", CheckStatus.FAIL, (("error", "timeout"), ("reason", "-"))
)
HOST = "host@example.nl"


def _platform(check: GroupCheck) -> tuple[Finding, ...]:
    """The findings of the platforms; the worker's own (ADR 0068) are tested apart."""
    return tuple(f for f in check.findings if f.area is not CheckArea.WORKER)


INTERVAL = timedelta(hours=24)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations()
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-2", name="Demo 2")
    # A live worker with the Webex adapters (ADR 0068), so the platform findings decide.
    state.worker_heartbeat = WorkerHeartbeat(NOW, "", (JobType.CREATE_VIDEO_ROOM.value,))
    return InMemoryUnitOfWork(state)


def _service(
    uow: InMemoryUnitOfWork, checker: FakeChecker | None, clock: FixedClock | None = None
) -> IntegrationCheckService:
    clock = clock or FixedClock(NOW)
    return IntegrationCheckService(
        uow, clock, AdminService(uow, clock), checker, SystemLog(uow, clock)
    )


def _with_host(uow: InMemoryUnitOfWork, *ids: int) -> None:
    for group_id in ids:
        uow.state.groups[group_id] = replace(uow.state.groups[group_id], video_host_email=HOST)


def _system_events(
    uow: InMemoryUnitOfWork,
) -> list[tuple[Outcome, str, str, str | None, str | None]]:
    return [
        (e.outcome, e.reason, e.message, e.organisation, e.group)
        for e in uow.state.audit
        if e.source is Source.SYSTEM
    ]


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

    assert _platform(check) == (OK, WARN, FAIL)
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

    assert [f.code for f in _platform(check)] == ["platform.not_configured"]
    assert [f.code for f in check.findings if f.area is CheckArea.WORKER] == [
        "worker.alive",
        "worker.jobs_ok",
    ]


async def test_every_check_proves_the_worker_and_the_queue(uow: InMemoryUnitOfWork) -> None:
    # ADR 0068: the probe ran in web; the bookings run in the worker.
    uow.state.worker_heartbeat = None

    check = await _service(uow, FakeChecker([OK])).run(ORG_ADMIN, "demo")

    assert check.findings[-2:] == (
        Finding(CheckArea.WORKER, "worker.never_seen", CheckStatus.FAIL),
        Finding(CheckArea.WORKER, "worker.jobs_ok", CheckStatus.OK),
    )
    assert check.overall is CheckStatus.FAIL


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
    assert _platform(last) == (OK,)


# --- ADR 0065 step 2: the automatic check -----------------------------------------------------


def test_a_failing_check_is_reported_with_the_first_failing_code_and_the_provider_reason() -> None:
    current = GroupCheck(1, NOW, (OK, WARN, REFUSED, FAIL))

    report = report_for(None, current)

    assert report == CheckReport(
        Outcome.FAILURE, "webex.schedule_refused", "timezone does not match"
    )
    # Every failing run is reported: the sweep itself is once per interval.
    assert report_for(current, current) == report


def test_a_failure_without_a_provider_reason_has_no_message() -> None:
    assert report_for(None, GroupCheck(1, NOW, (UNEXPLAINED,))) == CheckReport(
        Outcome.FAILURE, "webex.unreachable", ""
    )
    assert report_for(None, GroupCheck(1, NOW, (FAIL,))) == CheckReport(
        Outcome.FAILURE, "wxcc.schema.refused", ""
    )


def test_a_recovery_is_reported_once_as_a_success() -> None:
    failed = GroupCheck(1, NOW, (FAIL,))
    fine = GroupCheck(1, NOW, (OK, WARN))

    assert report_for(failed, fine) == CheckReport(Outcome.SUCCESS, "warn")
    assert report_for(fine, fine) is None
    assert report_for(None, fine) is None
    assert report_for(GroupCheck(1, NOW, (OK,)), fine) is None  # ok → warn is no event


def test_a_group_is_due_when_never_checked_or_checked_too_long_ago() -> None:
    assert check_due(None, NOW, INTERVAL)
    assert check_due(GroupCheck(1, NOW - INTERVAL, (OK,)), NOW, INTERVAL)
    assert not check_due(GroupCheck(1, NOW - INTERVAL + timedelta(minutes=1), (OK,)), NOW, INTERVAL)


async def test_the_system_check_stores_the_result_and_records_an_event_at_the_group(
    uow: InMemoryUnitOfWork,
) -> None:
    _with_host(uow, 1)
    checker = FakeChecker([OK, REFUSED])
    service = _service(uow, checker)

    check = await service.check_as_system("demo")

    assert check is not None
    assert _platform(check) == (OK, REFUSED)
    assert checker.calls == [("demo", ["demo", "demo-2"])]
    assert await service.last(ORG_ADMIN, "demo") == check  # the page shows it as the latest
    assert _system_events(uow) == [
        (Outcome.FAILURE, "webex.schedule_refused", "timezone does not match", "demo", "demo")
    ]
    assert uow.state.audit[-1].actor == SYSTEM_ACTOR


async def test_the_system_check_reports_every_failure_and_the_recovery_once(
    uow: InMemoryUnitOfWork,
) -> None:
    _with_host(uow, 1)
    await _service(uow, FakeChecker([FAIL])).check_as_system("demo")
    await _service(uow, FakeChecker([FAIL])).check_as_system("demo")  # next interval
    await _service(uow, FakeChecker([OK])).check_as_system("demo")  # Control Hub fixed
    await _service(uow, FakeChecker([OK])).check_as_system("demo")

    assert [e[:2] for e in _system_events(uow)] == [
        (Outcome.FAILURE, "wxcc.schema.refused"),
        (Outcome.FAILURE, "wxcc.schema.refused"),
        (Outcome.SUCCESS, "ok"),
    ]


async def test_the_system_check_skips_groups_without_a_host_or_without_a_service_app(
    uow: InMemoryUnitOfWork,
) -> None:
    checker = FakeChecker([OK])
    assert await _service(uow, checker).check_as_system("demo") is None  # no host
    assert await _service(uow, checker).check_as_system("nope") is None  # unknown group
    _with_host(uow, 1)
    assert await _service(uow, None).check_as_system("demo") is None  # no Service App

    assert checker.calls == []
    assert uow.state.group_checks == {}
    assert _system_events(uow) == []


async def test_the_sweep_checks_only_groups_with_a_host_that_are_due(
    uow: InMemoryUnitOfWork,
) -> None:
    _with_host(uow, 1, 2)  # demo and andere-team; demo-2 has no host
    uow.state.group_checks[2] = GroupCheck(2, NOW - timedelta(hours=1), (OK,))  # fresh
    checker = FakeChecker([OK])
    service = _service(uow, checker)

    assert await service.check_due_groups(INTERVAL) == ["demo"]
    assert [slug for slug, _ in checker.calls] == ["demo"]
    assert await service.check_due_groups(INTERVAL) == []  # just checked
    assert await service.check_due_groups(timedelta(0)) == ["demo", "andere-team"]
    assert await _service(uow, None).check_due_groups(timedelta(0)) == []


async def test_the_watch_looks_every_ten_minutes_at_most(uow: InMemoryUnitOfWork) -> None:
    _with_host(uow, 1)
    clock = FixedClock(NOW)
    watch = IntegrationWatch(_service(uow, FakeChecker([OK]), clock), clock, lambda: INTERVAL)

    assert await watch.run() == ["demo"]
    assert await watch.run() is None
    clock.advance(LOOK_EVERY - timedelta(seconds=1))
    assert await watch.run() is None
    clock.advance(timedelta(seconds=1))
    assert await watch.run() == []  # looked again; demo is not due for a day
    clock.advance(INTERVAL)
    assert await watch.run() == ["demo"]


def _with_mapping(uow: InMemoryUnitOfWork, *ids: int) -> None:
    for group_id in ids:
        uow.state.groups[group_id] = replace(
            uow.state.groups[group_id],
            wxcc_channel="openplanner-video",
            wxcc_business_address="openplanner@example.biz",
        )


async def test_a_group_with_a_contact_center_mapping_is_checked_without_a_host(
    uow: InMemoryUnitOfWork,
) -> None:
    """ADR 0082 point 4: the automatic check covers every group with a chain to prove."""
    _with_mapping(uow, 1)  # demo: work items, no video host
    checker = FakeChecker([OK])
    service = _service(uow, checker)

    assert await service.check_as_system("demo") is not None
    assert await service.check_due_groups(timedelta(0)) == ["demo"]
    assert [slug for slug, _ in checker.calls] == ["demo", "demo"]


async def test_every_check_asks_the_worker_about_dispatch_jobs_when_the_group_dispatches(
    uow: InMemoryUnitOfWork,
) -> None:
    _with_mapping(uow, 1)  # the heartbeat of the fixture claims meeting jobs only

    check = await _service(uow, FakeChecker([OK])).run(ORG_ADMIN, "demo")

    assert "worker.no_dispatch_jobs" in {f.code for f in check.findings}
