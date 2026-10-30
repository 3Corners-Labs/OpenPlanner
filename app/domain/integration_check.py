"""The "Koppeling controleren" use case: check a group's integration chain, by hand or on its own.

Whoever may edit a group's settings (``group.edit``) may check them; both the check and its
latest result are behind the matrix (ADR 0014). The platforms are read through the
``IntegrationChecker`` port, never written, apart from the probe meeting (ADR 0065). The result
replaces the previous one and every run by hand is audited with its overall status, so admins
can see who checked and what came out.

    run(principal, slug)   authorise ─► checker.check_group(group, peers) ─► worker findings
                                     ─► store ─► audit
    last(principal, slug)  authorise ─► latest stored result (or None)

ADR 0068: every check also proves the worker, which runs the bookings' jobs while the probe
meeting ran here: its heartbeat, version and job types, and the group's failed and overdue
jobs (``app.domain.worker_check``).

ADR 0065 step 2: the platform also checks on its own, so a wrong host, licence or scope is
reported before a citizen's booking fails. The worker checks every group with a Webex host
whose latest check is older than ``integration_check_interval_hours`` (``IntegrationWatch``),
and the admin portal checks a group right after its host address changed. Those runs go
through ``check_as_system``: no principal, no admin audit, but a system event
``integration.check`` at the group's level whenever ``report_for`` says so: on every failing
run (once per interval while it lasts) and once when a failing group is fine again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal, Scope
from app.domain.checks import CheckArea, CheckStatus, Finding, GroupCheck
from app.domain.errors import DomainError, ErrorCode
from app.domain.event_context import bind_level, trace_scope
from app.domain.ports import Clock, IntegrationChecker, Transaction, UnitOfWork
from app.domain.scheduling import Group
from app.domain.system_log import SystemLog
from app.domain.tunable import value_of
from app.domain.worker_check import worker_findings

CHECK_EVENT = "integration.check"
"""Audit action of a check; the reason is the overall status (by hand) or the failing code."""

NOT_CONFIGURED = Finding(CheckArea.PLATFORM, "platform.not_configured", CheckStatus.SKIPPED)

LOOK_EVERY = timedelta(minutes=10)
"""How often the worker looks whether a group's check is due: the sweep's resolution, not a
tunable; the interval itself is the setting (lower bound one hour)."""

NO_REASON = "-"
"""What the checker puts in a finding's ``reason`` when the provider gave none."""

HEARTBEAT_MAX_AGE = timedelta(seconds=120)
"""Default age after which the worker's heartbeat counts as stale (the healthcheck's)."""
OVERDUE_AFTER = timedelta(minutes=10)
"""Default threshold for an overdue pending job (``job_overdue_minutes``)."""


@dataclass(frozen=True)
class CheckReport:
    """What the system event about an automatic check says.

    Attributes:
        outcome: Failure while the chain is broken, success once it is whole again.
        reason: The first failing finding's code, or the overall status on recovery.
        message: The provider's own (masked) reason for the failure, if it gave one.
    """

    outcome: Outcome
    reason: str
    message: str = ""


def report_for(previous: GroupCheck | None, current: GroupCheck) -> CheckReport | None:
    """Decide whether an automatic check is reported as a system event, and how.

    A failing check is always reported: the sweep runs once per interval, so that is the
    repeat while the failure lasts. A check that is not failing is reported once, as a
    success, when the previous one failed; warnings and skipped links are never events.

    Args:
        previous: The group's latest stored check before this run, if any.
        current: The check just made.
    """
    if current.overall is CheckStatus.FAIL:
        failing = next(f for f in current.findings if f.status is CheckStatus.FAIL)
        reason = dict(failing.params).get("reason", "")
        return CheckReport(Outcome.FAILURE, failing.code, "" if reason == NO_REASON else reason)
    if previous is not None and previous.overall is CheckStatus.FAIL:
        return CheckReport(Outcome.SUCCESS, current.overall.value)
    return None


def check_due(previous: GroupCheck | None, now: datetime, interval: timedelta) -> bool:
    """Whether a group is due for an automatic check: never checked, or not within ``interval``."""
    return previous is None or now - previous.checked_at >= interval


class IntegrationCheckService:
    """Application service for checking a group's Webex, WxCC and webhook configuration.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits the runs by hand.
        checker: Reads the platforms; ``None`` when no Service App is configured.
        system_log: Where the events about automatic runs go.
        version: ``APP_VERSION`` of this process, compared with the worker's (ADR 0068).
        heartbeat_max_age: How old the worker's heartbeat may be before it counts as stale.
        overdue_after: How long past its planned time a pending job counts as overdue.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        checker: IntegrationChecker | None,
        system_log: SystemLog,
        *,
        version: str = "",
        heartbeat_max_age: timedelta | Callable[[], timedelta] = HEARTBEAT_MAX_AGE,
        overdue_after: timedelta | Callable[[], timedelta] = OVERDUE_AFTER,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._checker = checker
        self._system_log = system_log
        self._version = version
        self._heartbeat_max_age = heartbeat_max_age
        self._overdue_after = overdue_after

    async def run(self, principal: Principal, slug: str) -> GroupCheck:
        """Check the group now and keep the result.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        organisation = await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await _group(tx, slug)
        check, _ = await self._check(group, organisation)
        outcome = Outcome.FAILURE if check.overall is CheckStatus.FAIL else Outcome.SUCCESS
        await self._admin.record(
            principal.subject, CHECK_EVENT, outcome, check.overall.value, organisation, slug
        )
        return check

    async def last(self, principal: Principal, slug: str) -> GroupCheck | None:
        """Return the latest check of the group, if it was ever checked.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await _group(tx, slug)
            return await tx.get_group_check(group.id)

    async def check_as_system(self, slug: str) -> GroupCheck | None:
        """Check the group on the platform's own initiative; record a system event when due.

        Nothing is checked, and ``None`` returned, without a Service App, for an unknown group
        or for a group without a Webex host: there is no chain to prove then, and a skipped
        result would hide the admin's own latest check.
        """
        if self._checker is None:
            return None
        async with self._uow.read() as tx:
            summary = await tx.get_group_summary(slug)
        if summary is None or not summary.group.video_host_email:
            return None
        bind_level(summary.organisation_slug, slug)
        check, previous = await self._check(summary.group, summary.organisation_slug)
        report = report_for(previous, check)
        if report is not None:
            await self._system_log.record(
                CHECK_EVENT,
                report.outcome,
                group_slug=slug,
                reason=report.reason,
                message=report.message,
            )
        return check

    async def check_due_groups(self, interval: timedelta) -> list[str]:
        """Check every group with a Webex host whose latest check is older than ``interval``.

        Returns:
            The slugs of the groups checked, in listing order.
        """
        if self._checker is None:
            return []
        now = self._clock.now()
        due: list[str] = []
        async with self._uow.read() as tx:
            for summary in await tx.list_groups(Scope(everything=True)):
                group = summary.group
                if not group.video_host_email:
                    continue
                if check_due(await tx.get_group_check(group.id), now, interval):
                    due.append(group.slug)
        checked = []
        for slug in due:
            # One trace per group, so its probe requests land at its level (ADR 0068).
            with trace_scope():
                bind_level(None, slug)
                if await self.check_as_system(slug) is not None:
                    checked.append(slug)
        return checked

    async def _check(self, group: Group, organisation: str) -> tuple[GroupCheck, GroupCheck | None]:
        """Run the checker for ``group`` and store the result; return it with the previous one."""
        async with self._uow.read() as tx:
            previous = await tx.get_group_check(group.id)
            peers = [
                summary.group
                for summary in await tx.list_groups(Scope(organisations=frozenset({organisation})))
            ]
        findings = (
            await self._checker.check_group(group, peers) if self._checker else [NOT_CONFIGURED]
        )
        findings += await self._worker(group)
        check = GroupCheck(group.id, self._clock.now(), tuple(findings))
        async with self._uow.locked() as tx:
            await tx.save_group_check(check)
        return check, previous

    async def _worker(self, group: Group) -> list[Finding]:
        """Return the worker and queue findings of ``group`` (ADR 0068)."""
        now = self._clock.now()
        overdue_after = value_of(self._overdue_after)
        async with self._uow.read() as tx:
            heartbeat = await tx.get_worker_heartbeat()
            counts = await tx.count_group_jobs(group.id, now - overdue_after)
        return worker_findings(
            heartbeat,
            counts,
            now=now,
            max_age=value_of(self._heartbeat_max_age),
            web_version=self._version,
            has_host=group.video_enabled,
            overdue_after=overdue_after,
        )

    async def _authorize(self, principal: Principal, slug: str) -> str:
        """Require ``group.edit`` on the group; return its organisation slug."""
        authorization = require(
            await self._admin.authorize(principal, Permission.GROUP_EDIT, group_slug=slug)
        )
        organisation = authorization.target.organisation
        if organisation is None:  # pragma: no cover - a group target always has one
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return organisation


class IntegrationWatch:
    """The worker's sweep: looks every ``LOOK_EVERY`` which groups are due and checks them.

    Args:
        service: Runs and stores the checks.
        clock: Current time.
        interval: How old a group's latest check may be (``integration_check_interval_hours``).
    """

    def __init__(
        self,
        service: IntegrationCheckService,
        clock: Clock,
        interval: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators; the first look is on the first run."""
        self._service = service
        self._clock = clock
        self._interval = interval
        self._looked_at: datetime | None = None

    async def run(self) -> list[str] | None:
        """Check the due groups when a look is due; return their slugs, ``None`` without a look."""
        now = self._clock.now()
        if self._looked_at is not None and now - self._looked_at < LOOK_EVERY:
            return None
        self._looked_at = now
        return await self._service.check_due_groups(value_of(self._interval))


async def _group(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug``.

    Raises:
        DomainError: ``group_not_found``.
    """
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group
