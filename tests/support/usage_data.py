"""Usage data for the admin report tests: bookings created and cancelled, rolled up."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import GroupRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.ports import Clock
from app.domain.usage import FactKind, UsageFact, UsageOutcome, hour_of
from app.domain.usage_rollup_service import UsageRollup
from tests.support.admin_site import Site

DAY = "2026-01-01"
HOUR = datetime(2026, 1, 1, 8, tzinfo=UTC)


async def fill(site: Site, slug: str, count: int) -> None:
    """Write ``count`` bookings created and cancelled for a group, and roll them up."""
    app: Any = site.app
    await fill_app(app, site.clock, slug, count)


async def fill_app(app: Any, clock: Clock, slug: str, count: int) -> None:
    """The same for any running application: its engine and its clock."""
    engine = app.state.engine
    async with AsyncSession(engine) as session:
        group = (await session.execute(select(GroupRow).where(GroupRow.slug == slug))).scalar_one()
        ids = (group.id, group.organisation_id)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        for _ in range(count):
            await tx.record_usage(
                UsageFact(
                    FactKind.BOOKING_CREATED, hour_of(HOUR), ids[1], ids[0], value_seconds=3600
                )
            )
            await tx.record_usage(
                UsageFact(
                    FactKind.BOOKING_CANCELLED,
                    hour_of(HOUR),
                    ids[1],
                    ids[0],
                    outcome=UsageOutcome.CANCELLED_BY_PLANNER,
                )
            )
    await UsageRollup(
        uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25
    ).run_if_due()
