"""UpdateService (ADR 0029): hourly check, superuser requests, host updater claim and report.

Abuse cases from docs/threat-model.md: an organisation admin requesting an update, a second
request while one is open, a downgrade disguised as an update, a restore outside its window,
and an updater report that claims success for the wrong version.
"""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import IntegrationError
from app.domain.update_service import UPDATER_ACTOR, UpdateService
from app.domain.updates import (
    CHECK_INTERVAL,
    RESTORE_WINDOW,
    Release,
    ReleaseCheck,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
    UpdateStep,
    Version,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW

V1 = Version(1, 3, 0)
V2 = Version(1, 4, 0)
RELEASE = Release(V2, "https://github.com/acme/openplanner/releases/tag/v1.4.0")


class FakeReleases:
    def __init__(self, release: Release | None = RELEASE) -> None:
        self.release = release
        self.error: IntegrationError | None = None
        self.calls = 0

    async def latest(self) -> Release | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.release


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def releases() -> FakeReleases:
    return FakeReleases()


def service(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    releases: FakeReleases | None = None,
    current: Version | None = V1,
) -> UpdateService:
    return UpdateService(uow, clock, AdminService(uow, clock), current=current, releases=releases)


async def checked(uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases) -> None:
    assert await service(uow, clock, releases).check_if_due()


async def test_check_stores_the_latest_release_once_per_hour(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    updates = service(uow, clock, releases)
    assert await updates.check_if_due()
    assert uow.state.release_check == ReleaseCheck(NOW, RELEASE)
    clock.advance(CHECK_INTERVAL - timedelta(minutes=1))
    assert not await updates.check_if_due()
    clock.advance(timedelta(minutes=1))
    assert await updates.check_if_due()
    assert releases.calls == 2


async def test_failed_check_keeps_the_known_release_and_records_the_code(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    clock.advance(CHECK_INTERVAL)
    releases.error = IntegrationError("unauthorized", retryable=False)
    assert await service(uow, clock, releases).check_if_due()
    assert uow.state.release_check == ReleaseCheck(clock.now(), RELEASE, "unauthorized")


async def test_no_check_without_a_release_source(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    assert not await service(uow, clock).check_if_due()
    assert uow.state.release_check is None


async def test_notice_shows_the_superuser_a_newer_release(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    notice = await service(uow, clock).notice(DEMO_SUPERUSER)
    assert notice is not None
    assert notice.available == RELEASE


async def test_notice_is_nothing_for_other_admins_and_audits_nothing(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    assert await service(uow, clock).notice(DEMO_ORG_ADMIN) is None
    assert uow.state.audit == []  # the banner is shown on every page: no denial per page


async def test_notice_is_nothing_when_up_to_date(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    assert await service(uow, clock, current=V2).notice(DEMO_SUPERUSER) is None


async def test_superuser_requests_the_available_update_and_it_is_audited(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    created = await service(uow, clock).request_update(DEMO_SUPERUSER)
    assert (created.action, created.target, created.from_version) == (
        UpdateAction.UPDATE,
        V2,
        V1,
    )
    assert created.status is UpdateStatus.REQUESTED
    assert created.requested_by == DEMO_SUPERUSER.subject
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.detail) == (
        "system.update",
        Outcome.SUCCESS,
        "update.request",
    )


async def test_org_admin_cannot_request_an_update(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    with pytest.raises(DomainError) as caught:
        await service(uow, clock).request_update(DEMO_ORG_ADMIN)
    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.update_requests == {}
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_no_update_request_without_a_newer_release(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    for current in (V2, Version(2, 0, 0), None):
        with pytest.raises(DomainError) as caught:
            await service(uow, clock, current=current).request_update(DEMO_SUPERUSER)
        assert caught.value.code is ErrorCode.UPDATE_NOT_AVAILABLE


async def test_only_one_open_request(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    updates = service(uow, clock)
    await updates.request_update(DEMO_SUPERUSER)
    with pytest.raises(DomainError) as caught:
        await updates.request_update(DEMO_SUPERUSER)
    assert caught.value.code is ErrorCode.UPDATE_IN_PROGRESS


async def test_cancel_a_waiting_request(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    updates = service(uow, clock)
    created = await updates.request_update(DEMO_SUPERUSER)
    cancelled = await updates.cancel(DEMO_SUPERUSER, created.id or 0)
    assert cancelled.status is UpdateStatus.CANCELLED
    assert uow.state.audit[-1].detail == "request.cancel"
    with pytest.raises(DomainError) as caught:
        await updates.cancel(DEMO_SUPERUSER, 999)
    assert caught.value.code is ErrorCode.UPDATE_REQUEST_NOT_FOUND


async def test_updater_claims_the_waiting_request_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    updates = service(uow, clock)
    created = await updates.request_update(DEMO_SUPERUSER)
    started = await updates.claim()
    assert started is not None
    assert (started.id, started.status) == (created.id, UpdateStatus.RUNNING)
    assert await updates.claim() is None
    event = uow.state.audit[-1]
    assert (event.actor, event.detail) == (UPDATER_ACTOR, "update.start")


async def running(uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases) -> int:
    """Request an update and let the updater claim it; return the request id."""
    await checked(uow, clock, releases)
    created = await service(uow, clock).request_update(DEMO_SUPERUSER)
    await service(uow, clock).claim()
    return created.id or 0


async def finish_update(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> UpdateRequest:
    """Request, claim and report success as the new version (V2) would."""
    request_id = await running(uow, clock, releases)
    return await service(uow, clock, current=V2).report(request_id, UpdateStatus.SUCCEEDED, "")


async def test_report_success_from_the_new_version(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    done = await finish_update(uow, clock, releases)
    assert done.status is UpdateStatus.SUCCEEDED
    assert uow.state.audit[-1].outcome is Outcome.SUCCESS


async def test_success_reported_by_the_wrong_version_is_recorded_as_failed(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    request_id = await running(uow, clock, releases)
    done = await service(uow, clock, current=V1).report(request_id, UpdateStatus.SUCCEEDED, "")
    assert (done.status, done.reason) == (UpdateStatus.FAILED, "version_mismatch")
    assert uow.state.audit[-1].outcome is Outcome.FAILURE


async def test_report_rollback_with_its_reason(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    request_id = await running(uow, clock, releases)
    done = await service(uow, clock).report(
        request_id, UpdateStatus.ROLLED_BACK, "health_check_failed"
    )
    assert (done.status, done.reason) == (UpdateStatus.ROLLED_BACK, "health_check_failed")
    notice = await service(uow, clock).notice(DEMO_SUPERUSER)
    assert notice is not None
    assert notice.outcome == done


async def test_updater_reports_its_steps(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    request_id = await running(uow, clock, releases)
    clock.advance(timedelta(seconds=30))
    moved = await service(uow, clock).report_step(request_id, UpdateStep.VERIFYING)
    assert (moved.step, moved.step_at) == (UpdateStep.VERIFYING, clock.now())
    overview = await service(uow, clock).overview(DEMO_SUPERUSER)
    assert overview.open == moved
    event = uow.state.audit[-1]
    assert (event.actor, event.detail, event.at) == (UPDATER_ACTOR, "update.verifying", clock.now())


async def test_a_repeated_step_is_not_audited_again(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    request_id = await running(uow, clock, releases)
    await service(uow, clock).report_step(request_id, UpdateStep.PULLING)
    count = len(uow.state.audit)
    await service(uow, clock).report_step(request_id, UpdateStep.PULLING)
    assert len(uow.state.audit) == count


async def test_a_step_going_back_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    request_id = await running(uow, clock, releases)
    await service(uow, clock).report_step(request_id, UpdateStep.STARTING)
    with pytest.raises(DomainError) as caught:
        await service(uow, clock).report_step(request_id, UpdateStep.PULLING)
    assert caught.value.field == "step"


async def test_report_for_an_unknown_request(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    with pytest.raises(DomainError) as caught:
        await service(uow, clock).report(42, UpdateStatus.FAILED, "updater_error")
    assert caught.value.code is ErrorCode.UPDATE_REQUEST_NOT_FOUND


async def test_restore_previous_version_within_the_window(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await finish_update(uow, clock, releases)
    created = await service(uow, clock, current=V2).request_restore(DEMO_SUPERUSER)
    assert (created.action, created.target, created.from_version) == (
        UpdateAction.RESTORE,
        V1,
        V2,
    )
    assert uow.state.audit[-1].detail == "restore.request"


async def test_no_restore_after_the_window(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await finish_update(uow, clock, releases)
    clock.advance(RESTORE_WINDOW)
    with pytest.raises(DomainError) as caught:
        await service(uow, clock, current=V2).request_restore(DEMO_SUPERUSER)
    assert caught.value.code is ErrorCode.UPDATE_NOT_AVAILABLE


async def test_overview_lists_history_and_restore_option(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    done = await finish_update(uow, clock, releases)
    found = await service(uow, clock, releases, current=V2).overview(DEMO_SUPERUSER)
    assert found.enabled
    assert found.current == V2
    assert found.available is None
    assert found.restore == done
    assert found.history == [done]
    with pytest.raises(DomainError):
        await service(uow, clock).overview(DEMO_ORG_ADMIN)


async def test_overview_flags_a_request_no_updater_picked_up(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await checked(uow, clock, releases)
    await service(uow, clock).request_update(DEMO_SUPERUSER)
    clock.advance(timedelta(minutes=10))
    found = await service(uow, clock).overview(DEMO_SUPERUSER)
    assert found.open is not None
    assert found.unclaimed
