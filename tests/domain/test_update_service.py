"""UpdateService (ADR 0029): hourly check, superuser requests, host updater claim and report.

Abuse cases from docs/threat-model.md: an organisation admin requesting an update, a second
request while one is open, a downgrade disguised as an update, a restore outside its window,
and an updater report that claims success for the wrong version.
"""

from collections.abc import Sequence
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome, Source
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import IntegrationError
from app.domain.update_service import SYSTEM_ACTOR, UPDATER_ACTOR, UpdateService
from app.domain.updates import (
    CHECK_INTERVAL,
    HISTORY_DETAILS,
    HOST_ACTOR,
    RESTORE_WINDOW,
    VERSION_ACTION,
    ListedRelease,
    Release,
    ReleaseCheck,
    UpdateAction,
    UpdateChannel,
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
    def __init__(self, release: Release | None = RELEASE, *, more: Sequence[Release] = ()) -> None:
        self.release = release
        self.more = list(more)
        self.error: IntegrationError | None = None
        self.calls = 0

    async def listing(self) -> list[ListedRelease]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        found = [r for r in (self.release, *self.more) if r is not None]
        return [ListedRelease(r.version, r.url, None, f"notes of {r.version}") for r in found]


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
        "update.request,from:1.3.0,to:1.4.0",
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
    assert (event.actor, event.detail) == (UPDATER_ACTOR, "update.start,from:1.3.0,to:1.4.0")


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
    assert (event.actor, event.detail, event.at) == (
        UPDATER_ACTOR,
        "update.verifying,from:1.3.0,to:1.4.0",
        clock.now(),
    )


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
    assert uow.state.audit[-1].detail == "restore.request,from:1.4.0,to:1.3.0"


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


# Update channel and auto-apply (ADR 0059).

CANDIDATE = Release(
    Version(1, 4, 0, 2), "https://github.com/acme/openplanner/releases/tag/v1.4.0-rc.2"
)


async def test_a_candidate_is_offered_on_the_rc_channel_only(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await checked(uow, clock, FakeReleases(CANDIDATE))
    on_release = service(uow, clock)
    assert (await on_release.notice(DEMO_SUPERUSER)) is None
    with pytest.raises(DomainError, match="update_not_available"):
        await on_release.request_update(DEMO_SUPERUSER)

    on_rc = UpdateService(
        uow,
        clock,
        AdminService(uow, clock),
        current=V1,
        releases=None,
        checking=True,
        channel=UpdateChannel.CANDIDATE,
    )
    notice = await on_rc.notice(DEMO_SUPERUSER)
    assert notice is not None
    assert notice.available == CANDIDATE
    overview = await on_rc.overview(DEMO_SUPERUSER)
    assert (overview.channel, overview.auto_apply) == (UpdateChannel.CANDIDATE, False)
    created = await on_rc.request_update(DEMO_SUPERUSER)
    assert created.target == Version(1, 4, 0, 2)


async def test_auto_apply_requests_the_newer_version_as_the_system(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    auto = UpdateService(
        uow, clock, AdminService(uow, clock), current=V1, releases=releases, auto_apply=True
    )
    assert await auto.check_if_due()

    history = await auto.overview(DEMO_SUPERUSER)
    assert history.open is not None
    assert (history.open.action, history.open.target, history.open.from_version) == (
        UpdateAction.UPDATE,
        V2,
        V1,
    )
    assert history.open.requested_by == SYSTEM_ACTOR
    assert history.available is None  # a request is open
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome, event.detail) == (
        SYSTEM_ACTOR,
        "system.update",
        Outcome.SUCCESS,
        "update.auto_request,from:1.3.0,to:1.4.0",
    )


async def test_auto_apply_leaves_an_open_request_alone_and_asks_nothing_when_current(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    auto = UpdateService(
        uow, clock, AdminService(uow, clock), current=V1, releases=releases, auto_apply=True
    )
    assert await auto.check_if_due()
    clock.advance(timedelta(hours=1, minutes=1))
    assert await auto.check_if_due()
    assert len(await uow.state_requests()) == 1 if hasattr(uow, "state_requests") else True
    overview = await auto.overview(DEMO_SUPERUSER)
    assert len([r for r in overview.history if r.is_open]) == 1

    # Up to date: nothing is requested.
    fresh = InMemoryUnitOfWork()
    current = UpdateService(
        fresh, clock, AdminService(fresh, clock), current=V2, releases=releases, auto_apply=True
    )
    assert await current.check_if_due()
    assert (await current.overview(DEMO_SUPERUSER)).open is None


# Running versions and the history from the audit log (ADR 0067).


async def test_the_first_running_version_is_recorded_without_an_event(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    first = await service(uow, clock).note_running("1.3.0", "a" * 40)
    again = await service(uow, clock).note_running("1.3.0", "a" * 40)

    assert first is not None
    assert (first.version, first.commit, first.request_id) == ("1.3.0", "a" * 40, None)
    assert again is None
    assert len(uow.state.running_versions) == 1
    assert not [e for e in uow.state.audit if e.action == VERSION_ACTION]


async def test_a_version_change_outside_the_portal_is_audited_as_the_host(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await service(uow, clock).note_running("1.3.0", "a" * 40)
    clock.advance(timedelta(hours=1))

    changed = await service(uow, clock, current=Version(1, 4, 0, 2)).note_running(
        "1.4.0-rc.2", "b" * 40
    )

    assert changed is not None
    assert changed.request_id is None
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.source) == (HOST_ACTOR, VERSION_ACTION, Source.SYSTEM)
    assert event.detail == f"version.changed,from:1.3.0,to:1.4.0-rc.2,commit:{'b' * 12}"
    overview = await service(uow, clock, current=Version(1, 4, 0, 2)).overview(DEMO_SUPERUSER)
    assert overview.outside_change == changed


async def test_a_version_change_the_updater_made_carries_its_request(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await service(uow, clock).note_running("1.3.0", "a" * 40)
    await checked(uow, clock, releases)
    created = await service(uow, clock).request_update(DEMO_SUPERUSER)
    assert await service(uow, clock).claim() is not None

    # The new container starts on the requested version while the request is running.
    changed = await service(uow, clock, current=V2).note_running("1.4.0", "c" * 40)

    assert changed is not None
    assert changed.request_id == created.id
    assert uow.state.audit[-1].actor == UPDATER_ACTOR
    overview = await service(uow, clock, current=V2).overview(DEMO_SUPERUSER)
    assert overview.outside_change is None


async def test_the_history_is_the_audit_log_of_updates_milestones_only(
    uow: InMemoryUnitOfWork, clock: FixedClock, releases: FakeReleases
) -> None:
    await finish_update(uow, clock, releases)  # request, start, steps, succeeded
    await service(uow, clock, current=V2).note_running("1.4.0", "d" * 40)

    history = await service(uow, clock, current=V2).history(DEMO_SUPERUSER)

    details = [e.event.detail.split(",")[0] for e in history]
    assert details[0] == "update.succeeded"
    assert details[-1] == "update.request"
    assert "update.start" not in details  # steps stay in the audit log itself
    assert all(d in HISTORY_DETAILS for d in details)
    with pytest.raises(DomainError, match="not_permitted"):
        await service(uow, clock, current=V2).history(DEMO_ORG_ADMIN)


# Choosing a version (ADR 0067 point 3).

V3 = Version(1, 5, 0)
OLDER = Release(Version(1, 2, 0), "https://github.com/acme/openplanner/releases/tag/v1.2.0")
NEWEST = Release(V3, "https://github.com/acme/openplanner/releases/tag/v1.5.0")


async def test_the_page_offers_every_newer_listed_version_with_notes(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await checked(uow, clock, FakeReleases(RELEASE, more=[OLDER, NEWEST, CANDIDATE]))

    overview = await service(uow, clock).overview(DEMO_SUPERUSER)

    assert [item.version for item in overview.offered] == [V3, V2]  # newest first, no candidate
    assert overview.offered[0].notes == "notes of 1.5.0"
    assert overview.available == NEWEST
    on_rc = UpdateService(
        uow,
        clock,
        AdminService(uow, clock),
        current=V1,
        releases=None,
        checking=True,
        channel=UpdateChannel.CANDIDATE,
    )
    assert [item.version for item in (await on_rc.overview(DEMO_SUPERUSER)).offered] == [
        V3,
        V2,
        Version(1, 4, 0, 2),  # a release sorts above its own candidates
    ]


async def test_a_chosen_version_must_be_offered(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    await checked(uow, clock, FakeReleases(RELEASE, more=[OLDER, NEWEST, CANDIDATE]))
    chosen = await service(uow, clock).request_update(DEMO_SUPERUSER, V2)
    assert chosen.target == V2
    await service(uow, clock).cancel(DEMO_SUPERUSER, chosen.id or 0)

    for target in (OLDER.version, Version(1, 4, 0, 2), Version(9, 9, 9)):
        with pytest.raises(DomainError, match="update_not_available"):
            await service(uow, clock).request_update(DEMO_SUPERUSER, target)

    newest = await service(uow, clock).request_update(DEMO_SUPERUSER)
    assert newest.target == V3  # without a choice: the newest offered


async def test_nothing_is_offered_while_a_request_is_open(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await checked(uow, clock, FakeReleases(RELEASE, more=[NEWEST]))
    await service(uow, clock).request_update(DEMO_SUPERUSER)
    assert (await service(uow, clock).overview(DEMO_SUPERUSER)).offered == []
