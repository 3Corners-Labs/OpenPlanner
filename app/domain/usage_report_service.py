"""The use cases of the usage reports: who may read what, and the tables they get (ADR 0117).

    authorise (``reports.view``, audited on denial) ─► the groups in the reader's scope
    ─► the hourly cells of those groups ─► the report builder (small cells hidden)

The builders are ``volume_report``, ``queue_report``, ``capacity_report`` and
``channels_report``; all four read through the same ``load`` and so through the same scope.

Every read takes the ``Scope`` that the permission gives (ADR 0014); the adapter filters by it,
so a report never reads a group outside it. The threshold that hides small cells is the
larger of the platform value and the organisation's own, and never below 3 (ADR 0117 point 8).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.admin import AdminService, require
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal, Reason, Scope, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UsageUnitOfWork
from app.domain.usage import UsageCell
from app.domain.usage_report import (
    MAX_DAYS,
    DayStatusCount,
    ReportGroup,
    VolumeReport,
    created_in_range,
    headline_count,
    volume_report,
)
from app.domain.usage_report_capacity import CapacityReport, capacity_report
from app.domain.usage_report_channels import ChannelsReport, channels_report
from app.domain.usage_report_queue import QueueReport, queue_report
from app.domain.usage_suppress import effective_threshold

DEFAULT_DAYS = 28
"""The range a report opens with: the last four weeks, ending today."""

_MARGIN = timedelta(days=1)
"""The cells are read a day wider than the range on each side: a group-local day starts up to
14 hours away from the UTC day, and the report keeps only the days of its range."""


@dataclass(frozen=True)
class UsageScreen[R]:
    """A report and what it is about.

    Attributes:
        report: The tables, small cells hidden.
        groups: The groups the figures add up, in scope.
        organisation_slug: The organisation of those groups.
        organisation_name: Its name.
        group: The one group of a group page, or ``None`` on the organisation's page.
    """

    report: R
    groups: tuple[ReportGroup, ...]
    organisation_slug: str
    organisation_name: str
    group: ReportGroup | None


UsageVolume = UsageScreen[VolumeReport]
"""The *Volume* screen's report."""


@dataclass(frozen=True)
class LoadedUsage:
    """What every report, export and digest reads before it builds its tables."""

    groups: list[ReportGroup]
    first: date
    last: date
    cells: list[UsageCell]
    before: list[DayStatusCount]
    as_of: datetime | None
    threshold: int
    now: datetime


def build_volume(loaded: LoadedUsage) -> VolumeReport:
    """Return the *Volume* report of what was loaded, small cells hidden."""
    return volume_report(
        loaded.cells,
        loaded.groups,
        loaded.first,
        loaded.last,
        threshold=loaded.threshold,
        now=loaded.now,
        as_of=loaded.as_of,
        before_reporting=loaded.before,
    )


def build_queue(loaded: LoadedUsage) -> QueueReport:
    """Return the *Queue and calls* report of what was loaded, small cells hidden."""
    return queue_report(
        loaded.cells,
        loaded.groups,
        loaded.first,
        loaded.last,
        threshold=loaded.threshold,
        as_of=loaded.as_of,
    )


def build_capacity(loaded: LoadedUsage) -> CapacityReport:
    """Return the *Capacity* report of what was loaded, small cells hidden."""
    return capacity_report(
        loaded.cells,
        loaded.groups,
        loaded.first,
        loaded.last,
        threshold=loaded.threshold,
        as_of=loaded.as_of,
    )


def build_channels(loaded: LoadedUsage) -> ChannelsReport:
    """Return the *Channels* report of what was loaded, small cells hidden."""
    return channels_report(
        loaded.cells,
        loaded.groups,
        loaded.first,
        loaded.last,
        threshold=loaded.threshold,
        as_of=loaded.as_of,
    )


BUILDERS: dict[str, Callable[[LoadedUsage], object]] = {
    "volume": build_volume,
    "queue": build_queue,
    "capacity": build_capacity,
    "channels": build_channels,
}
"""The report families by their ``view`` code, for what builds one without a screen (export)."""


@dataclass(frozen=True)
class GroupHeadline:
    """One group on the *Reports* list: bookings created in the last four weeks."""

    group: ReportGroup
    created: int | None
    threshold: int


class UsageReportService:
    """Reads the usage reports for an admin, inside the scope of ``reports.view``.

    Args:
        uow: Opens read transactions with the usage queries.
        admin: Decides the permission and audits a denial.
        clock: Current time.
        platform_min: ``reporting_min_cell_size`` (class C, read each time).
        organisation_min: ``reporting_org_min_cell_size`` of an organisation id (class D).
    """

    def __init__(
        self,
        uow: UsageUnitOfWork,
        admin: AdminService,
        clock: Clock,
        platform_min: Callable[[], int],
        organisation_min: Callable[[int], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._admin = admin
        self._clock = clock
        self._platform_min = platform_min
        self._organisation_min = organisation_min

    def _threshold(self, organisation_id: int) -> int:
        """Return the small-cell threshold that applies to one organisation's figures."""
        return effective_threshold(self._platform_min(), self._organisation_min(organisation_id))

    async def load(
        self,
        principal: Principal,
        organisation_slug: str | None,
        group_slug: str | None,
        first_day: date | None,
        last_day: date | None,
        permission: Permission = Permission.REPORTS_VIEW,
    ) -> LoadedUsage:
        """Authorise ``permission`` (a view or its export), then read the groups and cells.

        Without a range the report covers the last ``DEFAULT_DAYS`` days up to today; days are
        whole group-local days.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted`` (a denial is audited), and
                ``invalid_range`` for a range that is backwards or wider than ``MAX_DAYS``.
        """
        if group_slug is not None:
            require(await self._admin.authorize(principal, permission, group_slug=group_slug))
        else:
            require(await self._admin.authorize_anywhere(principal, permission))
        scope = scope_for(principal, permission)
        if group_slug is None and organisation_slug is not None:
            await self._require_organisation(principal, scope, organisation_slug, permission)
        async with self._uow.read() as tx:
            groups = [
                g
                for g in await tx.list_report_groups(scope)
                if (group_slug is None or g.slug == group_slug)
                and (organisation_slug is None or g.organisation_slug == organisation_slug)
            ]
            if not groups:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            organisation_id = groups[0].organisation_id
            groups = [g for g in groups if g.organisation_id == organisation_id]
            first, last = self._range(groups[0], first_day, last_day)
            ids = [g.group_id for g in groups]
            lower = datetime.combine(first, datetime.min.time(), ZoneInfo("UTC")) - _MARGIN
            upper = datetime.combine(last, datetime.min.time(), ZoneInfo("UTC")) + 2 * _MARGIN
            cells = await tx.read_usage_cells(scope, ids, lower, upper)
            before = await tx.read_day_status_counts(scope, ids, first, last)
            state = await tx.usage_state()
        return LoadedUsage(
            groups,
            first,
            last,
            cells,
            before,
            state.rolled_at,
            self._threshold(organisation_id),
            self._clock.now(),
        )

    @staticmethod
    def _screen[R](report: R, loaded: LoadedUsage, group_slug: str | None) -> UsageScreen[R]:
        """Wrap ``report`` with the groups it adds up."""
        groups = loaded.groups
        one = groups[0] if group_slug is not None else None
        return UsageScreen(
            report, tuple(groups), groups[0].organisation_slug, groups[0].organisation_name, one
        )

    async def volume(
        self,
        principal: Principal,
        *,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
        first_day: date | None = None,
        last_day: date | None = None,
    ) -> UsageVolume:
        """Return the *Volume* report of one group, or of an organisation's groups in scope.

        Raises:
            DomainError: As ``load``.
        """
        loaded = await self.load(principal, organisation_slug, group_slug, first_day, last_day)
        report = build_volume(loaded)
        return self._screen(report, loaded, group_slug)

    async def queue(
        self,
        principal: Principal,
        *,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
        first_day: date | None = None,
        last_day: date | None = None,
    ) -> UsageScreen[QueueReport]:
        """Return the *Queue and calls* report (Q1 to Q9, U8, U11).

        Raises:
            DomainError: As ``load``.
        """
        loaded = await self.load(principal, organisation_slug, group_slug, first_day, last_day)
        report = build_queue(loaded)
        return self._screen(report, loaded, group_slug)

    async def capacity(
        self,
        principal: Principal,
        *,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
        first_day: date | None = None,
        last_day: date | None = None,
    ) -> UsageScreen[CapacityReport]:
        """Return the *Capacity* report (C1 to C5).

        Raises:
            DomainError: As ``load``.
        """
        loaded = await self.load(principal, organisation_slug, group_slug, first_day, last_day)
        report = build_capacity(loaded)
        return self._screen(report, loaded, group_slug)

    async def channels(
        self,
        principal: Principal,
        *,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
        first_day: date | None = None,
        last_day: date | None = None,
    ) -> UsageScreen[ChannelsReport]:
        """Return the *Channels* report (M1, M2, T1).

        Raises:
            DomainError: As ``load``.
        """
        loaded = await self.load(principal, organisation_slug, group_slug, first_day, last_day)
        report = build_channels(loaded)
        return self._screen(report, loaded, group_slug)

    async def _require_organisation(
        self, principal: Principal, scope: Scope, organisation_slug: str, permission: Permission
    ) -> None:
        """Refuse an organisation page to a reader whose scope is not the whole organisation.

        A group admin reads the groups they hold, one page each; the organisation's page adds
        up its groups and is for who holds ``reports.view`` on the organisation (or on all). The
        refusal is audited like the matrix's own.

        Raises:
            DomainError: ``not_permitted``.
        """
        if scope.everything or organisation_slug in scope.organisations:
            return
        await self._admin.record(
            principal.subject,
            permission.value,
            Outcome.DENIED,
            Reason.OUT_OF_SCOPE.value,
            organisation=organisation_slug,
        )
        raise DomainError(ErrorCode.NOT_PERMITTED)

    def _range(
        self, group: ReportGroup, first_day: date | None, last_day: date | None
    ) -> tuple[date, date]:
        """Return the validated range, defaulting to the last four weeks up to today."""
        today = self._clock.now().astimezone(ZoneInfo(group.timezone)).date()
        last = last_day or today
        first = first_day or last - timedelta(days=DEFAULT_DAYS - 1)
        if first > last or (last - first).days + 1 > MAX_DAYS:
            raise DomainError(ErrorCode.INVALID_RANGE)
        return first, last

    async def headlines(self, principal: Principal) -> list[GroupHeadline]:
        """Return, per group in scope, the bookings created in the last four weeks.

        A count below the organisation's threshold is hidden (``None``); there is no total
        over groups, so nothing can be subtracted back.

        Raises:
            DomainError: ``not_permitted`` (a denial is audited).
        """
        require(await self._admin.authorize_anywhere(principal, Permission.REPORTS_VIEW))
        scope = scope_for(principal, Permission.REPORTS_VIEW)
        async with self._uow.read() as tx:
            groups = await tx.list_report_groups(scope)
            if not groups:
                return []
            now = self._clock.now()
            cells = await tx.read_usage_cells(
                scope,
                [g.group_id for g in groups],
                now - timedelta(days=DEFAULT_DAYS + 1),
                now + _MARGIN,
            )
        by_group: dict[int, list[UsageCell]] = {}
        for cell in cells:
            if cell.metric == "U1":
                by_group.setdefault(cell.group_id, []).append(cell)
        rows: list[GroupHeadline] = []
        for group in groups:
            first, last = self._range(group, None, None)
            total = created_in_range(by_group.get(group.group_id, ()), group, first, last)
            threshold = self._threshold(group.organisation_id)
            rows.append(GroupHeadline(group, headline_count(total, threshold), threshold))
        return rows
