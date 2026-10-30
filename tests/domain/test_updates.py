"""Update rules (ADR 0029, ADR 0048): versions, the hourly check, the request queue, the
restore window and the host updater's steps."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.updates import (
    CHECK_INTERVAL,
    OUTCOME_SHOWN,
    RESTORE_WINDOW,
    UNCLAIMED_AFTER,
    PlanStep,
    Release,
    ReleaseCheck,
    UpdateAction,
    UpdateChannel,
    UpdateRequest,
    UpdateStatus,
    UpdateStep,
    Version,
    available_update,
    check_due,
    claimed,
    finished,
    open_request,
    progress,
    progressed,
    recent_outcome,
    restore_candidate,
    unclaimed,
)
from tests.support.domain import NOW

V1 = Version(1, 3, 0)
V2 = Version(1, 4, 0)
RELEASE = Release(V2, "https://github.com/acme/openplanner/releases/tag/v1.4.0")


def request(**overrides: object) -> UpdateRequest:
    fields: dict[str, object] = {
        "id": 1,
        "action": UpdateAction.UPDATE,
        "target": V2,
        "from_version": V1,
        "requested_by": "s-1",
        "requested_at": NOW,
        "status": UpdateStatus.REQUESTED,
    }
    fields.update(overrides)
    return UpdateRequest(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.4.0", Version(1, 4, 0)),
        ("v1.4.0", Version(1, 4, 0)),
        ("v10.0.12", Version(10, 0, 12)),
        # Release candidates (ADR 0059).
        ("1.4.0-rc.1", Version(1, 4, 0, 1)),
        ("v0.4.0-rc.12", Version(0, 4, 0, 12)),
    ],
)
def test_parse_accepts_release_versions(raw: str, expected: Version) -> None:
    assert Version.parse(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "main",
        "1.4",
        "v1.4.0 ",
        "01.4.0",
        "1.4.0.1",
        "1" * 30 + ".0.0",
        # Only CI's -rc.N form is a candidate; other pre-release spellings are not versions.
        "1.4.0-rc.0",
        "1.4.0-rc1",
        "1.4.0-beta.1",
        "1.4.0-rc.1+build",
    ],
)
def test_parse_refuses_anything_else(raw: str) -> None:
    assert Version.parse(raw) is None


def test_versions_order_numerically_and_print_without_prefix() -> None:
    assert Version(1, 10, 0) > Version(1, 9, 9)
    assert str(Version(1, 4, 0)) == "1.4.0"


def test_candidates_order_as_semver_says_and_print_their_number() -> None:
    # A candidate comes before its release and after the release before it.
    assert Version(0, 4, 0, 3) < Version(0, 4, 0)
    assert Version(0, 4, 0, 3) > Version(0, 3, 0)
    assert Version(0, 4, 0, 2) < Version(0, 4, 0, 10)
    assert Version(0, 4, 0, 1) >= Version(0, 4, 0, 1)
    assert Version(0, 4, 0, 1) != Version(0, 4, 0)
    assert Version(0, 4, 0, 3).is_candidate
    assert not Version(0, 4, 0).is_candidate
    assert str(Version(0, 4, 0, 3)) == "0.4.0-rc.3"
    assert sorted([Version(0, 4, 0), Version(0, 4, 0, 2), Version(0, 3, 0)]) == [
        Version(0, 3, 0),
        Version(0, 4, 0, 2),
        Version(0, 4, 0),
    ]


def test_a_candidate_is_offered_on_the_rc_channel_only() -> None:
    candidate = Release(Version(1, 4, 0, 2), "https://github.com/acme/openplanner/releases/tag/x")
    check = ReleaseCheck(NOW, candidate)
    assert available_update(V1, check) is None
    assert available_update(V1, check, UpdateChannel.CANDIDATE) == candidate
    # Running a candidate, the release of the same number is an update on both channels.
    release = ReleaseCheck(NOW, Release(Version(1, 4, 0), candidate.url))
    assert available_update(Version(1, 4, 0, 2), release) == release.latest
    assert available_update(Version(1, 4, 0, 2), release, UpdateChannel.CANDIDATE) == release.latest


def test_release_needs_a_github_https_page() -> None:
    with pytest.raises(DomainError) as caught:
        Release(V2, "javascript:alert(1)")
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_check_is_due_first_time_and_after_an_hour() -> None:
    last = ReleaseCheck(NOW, RELEASE)
    assert check_due(None, NOW)
    assert not check_due(last, NOW + CHECK_INTERVAL - timedelta(seconds=1))
    assert check_due(last, NOW + CHECK_INTERVAL)


def test_update_is_available_only_when_newer_than_a_release_build() -> None:
    check = ReleaseCheck(NOW, RELEASE)
    assert available_update(V1, check) == RELEASE
    assert available_update(V2, check) is None
    assert available_update(Version(2, 0, 0), check) is None
    assert available_update(None, check) is None  # development build: never offered
    assert available_update(V1, None) is None
    assert available_update(V1, ReleaseCheck(NOW, None, "unauthorized")) is None


def test_open_request_is_the_requested_or_running_one() -> None:
    done = request(id=1, status=UpdateStatus.SUCCEEDED, finished_at=NOW)
    running = request(id=2, status=UpdateStatus.RUNNING)
    assert open_request([running, done]) == running
    assert open_request([done]) is None


def test_claim_and_finish_follow_the_state_machine() -> None:
    started = claimed(request(), NOW)
    assert started.status is UpdateStatus.RUNNING
    assert started.started_at == NOW
    ended = finished(started, UpdateStatus.ROLLED_BACK, "health_check_failed", NOW)
    assert ended.status is UpdateStatus.ROLLED_BACK
    assert ended.reason == "health_check_failed"
    assert ended.finished_at == NOW


@pytest.mark.parametrize(
    ("current", "status"),
    [
        (UpdateStatus.REQUESTED, UpdateStatus.SUCCEEDED),  # never claimed
        (UpdateStatus.SUCCEEDED, UpdateStatus.FAILED),  # already finished
        (UpdateStatus.RUNNING, UpdateStatus.RUNNING),  # not an outcome
    ],
)
def test_finish_refuses_wrong_transitions(current: UpdateStatus, status: UpdateStatus) -> None:
    with pytest.raises(DomainError) as caught:
        finished(request(status=current), status, "", NOW)
    assert caught.value.code is ErrorCode.UPDATE_IN_PROGRESS


def test_claim_refuses_a_request_that_is_not_waiting() -> None:
    with pytest.raises(DomainError):
        claimed(request(status=UpdateStatus.CANCELLED), NOW)


@pytest.mark.parametrize("reason", ["Health failed", "x" * 41, "rm -rf", "naam@example.org"])
def test_finish_refuses_reasons_that_are_not_codes(reason: str) -> None:
    with pytest.raises(DomainError) as caught:
        finished(request(status=UpdateStatus.RUNNING), UpdateStatus.FAILED, reason, NOW)
    assert caught.value.field == "reason"


def test_cancel_only_while_requested() -> None:
    cancelled = finished(request(), UpdateStatus.CANCELLED, "", NOW)
    assert cancelled.status is UpdateStatus.CANCELLED
    with pytest.raises(DomainError):
        finished(request(status=UpdateStatus.RUNNING), UpdateStatus.CANCELLED, "", NOW)


def test_restore_is_offered_for_24_hours_after_a_successful_update() -> None:
    done = request(status=UpdateStatus.SUCCEEDED, finished_at=NOW)
    assert restore_candidate([done], V2, NOW + RESTORE_WINDOW - timedelta(seconds=1)) == done
    assert restore_candidate([done], V2, NOW + RESTORE_WINDOW) is None


def test_restore_needs_the_updated_version_to_be_running() -> None:
    done = request(status=UpdateStatus.SUCCEEDED, finished_at=NOW)
    assert restore_candidate([done], V1, NOW) is None


def test_restore_is_not_offered_after_a_restore_or_for_failed_updates() -> None:
    update = request(id=1, status=UpdateStatus.SUCCEEDED, finished_at=NOW)
    restore = request(
        id=2,
        action=UpdateAction.RESTORE,
        target=V1,
        from_version=V2,
        status=UpdateStatus.SUCCEEDED,
        finished_at=NOW,
    )
    rolled_back = request(id=3, status=UpdateStatus.ROLLED_BACK, finished_at=NOW)
    assert restore_candidate([restore, update], V1, NOW) is None
    assert restore_candidate([rolled_back], V1, NOW) is None


def test_unclaimed_after_ten_minutes() -> None:
    waiting = request()
    assert not unclaimed(waiting, NOW + UNCLAIMED_AFTER - timedelta(seconds=1))
    assert unclaimed(waiting, NOW + UNCLAIMED_AFTER)
    assert not unclaimed(replace(waiting, status=UpdateStatus.RUNNING), NOW + UNCLAIMED_AFTER)


def test_recent_outcome_is_the_latest_finished_request_for_a_day() -> None:
    done = request(status=UpdateStatus.FAILED, finished_at=NOW, reason="signature_invalid")
    cancelled = request(id=2, status=UpdateStatus.CANCELLED, finished_at=NOW)
    assert recent_outcome([cancelled, done], NOW + OUTCOME_SHOWN - timedelta(seconds=1)) == done
    assert recent_outcome([done], NOW + OUTCOME_SHOWN) is None
    assert recent_outcome([request()], NOW) is None


def states(request: UpdateRequest) -> list[tuple[UpdateStep, str]]:
    return [(item.step, item.state) for item in progress(request)]


def test_waiting_request_shows_the_waiting_step_as_current() -> None:
    assert states(request()) == [
        (UpdateStep.WAITING, "current"),
        (UpdateStep.PULLING, "pending"),
        (UpdateStep.VERIFYING, "pending"),
        (UpdateStep.BACKING_UP, "pending"),
        (UpdateStep.STARTING, "pending"),
    ]


def test_claimed_request_without_a_reported_step_is_at_its_first_step() -> None:
    running = claimed(request(action=UpdateAction.RESTORE, target=V1, from_version=V2), NOW)
    assert states(running) == [
        (UpdateStep.WAITING, "done"),
        (UpdateStep.BACKING_UP, "current"),
        (UpdateStep.MIGRATING, "pending"),
        (UpdateStep.STARTING, "pending"),
    ]


def test_reported_step_marks_the_earlier_ones_done() -> None:
    later = NOW + timedelta(minutes=1)
    moved = progressed(claimed(request(), NOW), UpdateStep.BACKING_UP, later)
    assert moved.step is UpdateStep.BACKING_UP
    assert moved.step_at == later
    assert states(moved)[2:] == [
        (UpdateStep.VERIFYING, "done"),
        (UpdateStep.BACKING_UP, "current"),
        (UpdateStep.STARTING, "pending"),
    ]
    assert progress(moved)[3] == PlanStep(UpdateStep.BACKING_UP, "current", later)


def test_rolling_back_is_added_after_the_plan() -> None:
    started = progressed(claimed(request(), NOW), UpdateStep.STARTING, NOW)
    back = progressed(started, UpdateStep.ROLLING_BACK, NOW)
    assert states(back)[-2:] == [
        (UpdateStep.STARTING, "done"),
        (UpdateStep.ROLLING_BACK, "current"),
    ]


def test_steps_only_move_forward_and_belong_to_the_action() -> None:
    started = progressed(claimed(request(), NOW), UpdateStep.BACKING_UP, NOW)
    assert progressed(started, UpdateStep.BACKING_UP, NOW + timedelta(seconds=5)) == started
    for step in (UpdateStep.VERIFYING, UpdateStep.MIGRATING, UpdateStep.WAITING):
        with pytest.raises(DomainError) as caught:
            progressed(started, step, NOW)
        assert caught.value.field == "step"


@pytest.mark.parametrize("status", [UpdateStatus.REQUESTED, UpdateStatus.SUCCEEDED])
def test_steps_only_while_running(status: UpdateStatus) -> None:
    with pytest.raises(DomainError) as caught:
        progressed(request(status=status), UpdateStep.PULLING, NOW)
    assert caught.value.code is ErrorCode.UPDATE_IN_PROGRESS


def test_finished_request_has_no_progress() -> None:
    assert progress(request(status=UpdateStatus.SUCCEEDED, finished_at=NOW)) == []
