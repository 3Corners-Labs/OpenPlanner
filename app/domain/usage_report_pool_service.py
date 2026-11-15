"""The use case of the pools part of the *Capacity* report (ADR 0122 step 5).

    authorise ``reports.view`` on the organisation (audited on denial)
    ─► the counted pools, their staffing and appointment times inside the reader's scope
    ─► ``pool_report`` (small cells hidden)

A pool belongs to an organisation and serves several services, so the figures are for a reader
who holds ``reports.view`` on the organisation (or on all), like the organisation's own usage
page; a reader of one service never sees them, because a pool's figures add up services the
reader may not see. The range is clipped to the days the appointment rows are still kept
(``booking_row_retention_days``): before that the rows are merged into daily counts, and a pool
read over them would look idle.
"""

from collections.abc import Callable
from datetime import date, timedelta

from app.domain.admin import AdminService, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, PoolReportUnitOfWork
from app.domain.usage_report import MAX_DAYS
from app.domain.usage_report_pools import PoolReport, pool_report
from app.domain.usage_report_service import require_organisation_scope
from app.domain.usage_suppress import effective_threshold


class PoolUsageService:
    """Reads the utilisation of an organisation's counted pools for an admin.

    Args:
        uow: Opens read transactions with the pool report's queries.
        admin: Decides the permission and audits a denial.
        clock: Current time.
        platform_min: ``reporting_min_cell_size`` (class C, read each time).
        organisation_min: ``reporting_org_min_cell_size`` of an organisation id (class D).
        retention_days: ``booking_row_retention_days`` (class C, read each time).
    """

    def __init__(
        self,
        uow: PoolReportUnitOfWork,
        admin: AdminService,
        clock: Clock,
        platform_min: Callable[[], int],
        organisation_min: Callable[[int], int],
        retention_days: Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._admin = admin
        self._clock = clock
        self._platform_min = platform_min
        self._organisation_min = organisation_min
        self._retention_days = retention_days

    async def report(
        self, principal: Principal, organisation_slug: str, first_day: date, last_day: date
    ) -> PoolReport | None:
        """Return the pools' utilisation over the days, or ``None`` when there is nothing to show.

        That is: no counted pool, or a range that ends before the appointment rows are kept.

        ``first_day`` moves forward to the oldest day the appointment rows are kept for.

        Raises:
            DomainError: ``not_permitted`` (audited) for a reader whose ``reports.view`` is not
                the organisation's, ``invalid_range`` for a backwards or too wide range.
        """
        permission = Permission.REPORTS_VIEW
        require(await self._admin.authorize_anywhere(principal, permission))
        scope = scope_for(principal, permission)
        await require_organisation_scope(
            self._admin, principal, scope, organisation_slug, permission
        )
        if first_day > last_day or (last_day - first_day).days + 1 > MAX_DAYS:
            raise DomainError(ErrorCode.INVALID_RANGE)
        now = self._clock.now()
        kept = now.date() - timedelta(days=self._retention_days())
        if last_day < kept:  # every day asked for is older than the rows: nothing to read
            return None
        first = max(first_day, kept)
        async with self._uow.read() as tx:
            found = await tx.read_pool_report_inputs(scope, organisation_slug, first, last_day)
        if found is None or not found.pools:
            return None
        threshold = effective_threshold(
            self._platform_min(), self._organisation_min(found.organisation_id)
        )
        return pool_report(found.pools, first, last_day, now=now, threshold=threshold)
