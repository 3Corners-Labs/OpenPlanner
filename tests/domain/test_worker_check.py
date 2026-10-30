"""The worker and the queue as links of the group check (ADR 0068)."""

from datetime import timedelta

import pytest

from app.domain.checks import CheckArea, CheckStatus, Finding
from app.domain.job_plan import JobCounts, JobType
from app.domain.worker_check import HEARTBEAT_EVERY, WorkerHeartbeat, heartbeat_due, worker_findings
from tests.support.domain import NOW

MAX_AGE = timedelta(seconds=120)
OVERDUE = timedelta(minutes=10)
VIDEO = (JobType.CREATE_VIDEO_ROOM.value,)
NONE = JobCounts()


def _codes(
    heartbeat: WorkerHeartbeat | None,
    counts: JobCounts = NONE,
    *,
    web: str = "1.0.0",
    has_host: bool = True,
) -> list[tuple[str, CheckStatus]]:
    findings = worker_findings(
        heartbeat,
        counts,
        now=NOW,
        max_age=MAX_AGE,
        web_version=web,
        has_host=has_host,
        overdue_after=OVERDUE,
    )
    assert all(f.area is CheckArea.WORKER for f in findings)
    return [(f.code, f.status) for f in findings]


def test_a_worker_that_never_reported_fails() -> None:
    assert _codes(None) == [
        ("worker.never_seen", CheckStatus.FAIL),
        ("worker.jobs_ok", CheckStatus.OK),
    ]


def test_a_stale_heartbeat_fails_with_its_age_and_version() -> None:
    old = WorkerHeartbeat(NOW - timedelta(minutes=7), "0.3.0", VIDEO)

    findings = worker_findings(
        old,
        JobCounts(),
        now=NOW,
        max_age=MAX_AGE,
        web_version="1.0.0",
        has_host=True,
        overdue_after=OVERDUE,
    )

    assert findings[0] == Finding(
        CheckArea.WORKER,
        "worker.stale",
        CheckStatus.FAIL,
        (("minutes", "7"), ("version", "0.3.0")),
    )


def test_a_live_worker_of_the_same_version_with_meeting_jobs_is_ok() -> None:
    assert _codes(WorkerHeartbeat(NOW, "1.0.0", VIDEO)) == [
        ("worker.alive", CheckStatus.OK),
        ("worker.jobs_ok", CheckStatus.OK),
    ]


def test_a_half_done_update_is_a_warning_naming_both_versions() -> None:
    findings = worker_findings(
        WorkerHeartbeat(NOW, "0.3.0", VIDEO),
        JobCounts(),
        now=NOW,
        max_age=MAX_AGE,
        web_version="0.4.0-rc.7",
        has_host=True,
        overdue_after=OVERDUE,
    )

    assert findings[1] == Finding(
        CheckArea.WORKER,
        "worker.version_differs",
        CheckStatus.WARN,
        (("worker", "0.3.0"), ("web", "0.4.0-rc.7")),
    )


def test_a_worker_without_the_webex_settings_fails_for_a_group_with_a_host() -> None:
    no_video = WorkerHeartbeat(NOW, "1.0.0", (JobType.PURGE_PII.value,))

    assert ("worker.no_video_jobs", CheckStatus.FAIL) in _codes(no_video)
    assert ("worker.no_video_jobs", CheckStatus.FAIL) not in _codes(no_video, has_host=False)


def test_failed_and_overdue_jobs_are_warnings_with_their_counts() -> None:
    findings = worker_findings(
        WorkerHeartbeat(NOW, "1.0.0", VIDEO),
        JobCounts(failed=2, overdue=3),
        now=NOW,
        max_age=MAX_AGE,
        web_version="1.0.0",
        has_host=True,
        overdue_after=OVERDUE,
    )

    assert findings[1:] == [
        Finding(CheckArea.WORKER, "worker.failed_jobs", CheckStatus.WARN, (("count", "2"),)),
        Finding(
            CheckArea.WORKER,
            "worker.overdue_jobs",
            CheckStatus.WARN,
            (("count", "3"), ("minutes", "10")),
        ),
    ]


@pytest.mark.parametrize(
    ("ago", "due"),
    [
        (None, True),
        (timedelta(0), False),
        (HEARTBEAT_EVERY - timedelta(seconds=1), False),
        (HEARTBEAT_EVERY, True),
    ],
)
def test_the_heartbeat_row_is_written_once_a_minute(ago: timedelta | None, due: bool) -> None:
    assert heartbeat_due(None if ago is None else NOW - ago, NOW) is due
