"""The worker and the job queue as links of a group's integration chain (ADR 0068).

The probe meeting of the group check runs in the ``web`` container; a booking's meeting is
made by the ``worker``. So the check also proves the worker: that it is alive, which version
it runs, that it claims the meeting jobs (it was started with the Webex settings) and that the
group's queue holds no failed or overdue jobs. The worker leaves a heartbeat row once a minute
(``WorkerHeartbeat``); ``worker_findings`` reads it and the counts and returns findings like
the platform checker does. Pure Python: the service feeds it what the database says.

    worker.never_seen       no heartbeat row at all                              fail
    worker.stale            heartbeat older than the healthcheck's maximum age   fail
    worker.alive            seen within that age, with its version                ok
    worker.version_differs  worker and web run different versions                warn
    worker.no_video_jobs    the group has a host; the worker claims no meeting   fail
    worker.no_dispatch_jobs the group dispatches; the worker claims no work item fail
    worker.jobs_ok          no failed or overdue jobs of the group                ok
    worker.failed_jobs      failed jobs of the group                              warn
    worker.overdue_jobs     pending jobs past their time by the threshold         warn
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.checks import CheckArea, CheckStatus, Finding
from app.domain.job_plan import JobCounts, JobType

HEARTBEAT_EVERY = timedelta(minutes=1)
"""How often the worker writes its heartbeat row: a constant of the loop (ADR 0068)."""


@dataclass(frozen=True)
class WorkerHeartbeat:
    """What the worker last reported about itself.

    Attributes:
        seen_at: When the row was written (UTC).
        version: ``APP_VERSION`` of the worker; empty in development.
        job_types: The job types the worker claims with its current adapters.
    """

    seen_at: datetime
    version: str
    job_types: tuple[str, ...] = ()


def heartbeat_due(last: datetime | None, now: datetime) -> bool:
    """Whether the worker should write its heartbeat row again."""
    return last is None or now - last >= HEARTBEAT_EVERY


def worker_findings(
    heartbeat: WorkerHeartbeat | None,
    counts: JobCounts,
    *,
    now: datetime,
    max_age: timedelta,
    web_version: str,
    has_host: bool,
    overdue_after: timedelta,
    dispatches: bool = False,
) -> list[Finding]:
    """Return the worker and queue findings of one group.

    Args:
        heartbeat: The worker's latest heartbeat row, if any.
        counts: The group's failed and overdue jobs.
        now: Current time.
        max_age: How old the heartbeat may be (``WORKER_HEARTBEAT_MAX_AGE_SECONDS``).
        web_version: The version of the process running the check.
        has_host: Whether the group has a Webex host, so meeting jobs are expected.
        overdue_after: The threshold used for ``counts.overdue`` (``job_overdue_minutes``).
        dispatches: Whether the group sends work items to a contact center, so dispatch jobs
            are expected (ADR 0082 point 4).
    """
    area = CheckArea.WORKER
    findings: list[Finding] = []
    if heartbeat is None:
        findings.append(Finding(area, "worker.never_seen", CheckStatus.FAIL))
    elif now - heartbeat.seen_at > max_age:
        minutes = str(int((now - heartbeat.seen_at) // timedelta(minutes=1)))
        findings.append(
            Finding(
                area,
                "worker.stale",
                CheckStatus.FAIL,
                (("minutes", minutes), ("version", heartbeat.version or "-")),
            )
        )
    else:
        findings.append(
            Finding(area, "worker.alive", CheckStatus.OK, (("version", heartbeat.version or "-"),))
        )
        if heartbeat.version != web_version:
            findings.append(
                Finding(
                    area,
                    "worker.version_differs",
                    CheckStatus.WARN,
                    (("worker", heartbeat.version or "-"), ("web", web_version or "-")),
                )
            )
        if has_host and JobType.CREATE_VIDEO_ROOM.value not in heartbeat.job_types:
            findings.append(Finding(area, "worker.no_video_jobs", CheckStatus.FAIL))
        if dispatches and JobType.DISPATCH_WXCC_TASK.value not in heartbeat.job_types:
            findings.append(Finding(area, "worker.no_dispatch_jobs", CheckStatus.FAIL))
    if counts.failed:
        findings.append(
            Finding(area, "worker.failed_jobs", CheckStatus.WARN, (("count", str(counts.failed)),))
        )
    if counts.overdue:
        minutes = str(int(overdue_after // timedelta(minutes=1)))
        findings.append(
            Finding(
                area,
                "worker.overdue_jobs",
                CheckStatus.WARN,
                (("count", str(counts.overdue)), ("minutes", minutes)),
            )
        )
    if not counts.failed and not counts.overdue:
        findings.append(Finding(area, "worker.jobs_ok", CheckStatus.OK))
    return findings
