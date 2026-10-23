"""Demo configuration: one organisation and one group with weekday opening hours.

Used by ``python -m app.cli seed-demo`` and by tests. Idempotent: an existing demo group is
left untouched.
"""

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import GroupRow, OrganisationRow, ReminderRuleRow, ScheduleRuleRow

DEMO_SLUG = "demo"
DEMO_WINDOWS = ((time(9), time(12)), (time(13), time(17)))
DEMO_CAPACITY = 2


async def seed_demo(engine: AsyncEngine) -> bool:
    """Create the demo organisation and group; return ``False`` if they already exist."""
    async with AsyncSession(engine) as session, session.begin():
        existing = await session.scalar(select(GroupRow.id).where(GroupRow.slug == DEMO_SLUG))
        if existing is not None:
            return False
        organisation = OrganisationRow(name="Demo-gemeente", slug=DEMO_SLUG)
        session.add(organisation)
        await session.flush()
        group = GroupRow(
            organisation_id=organisation.id,
            name="Demo: videogesprek",
            slug=DEMO_SLUG,
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
            wxcc_queue_id="demo-queue",
            dispatch_lead_minutes=5,
        )
        session.add(group)
        await session.flush()
        for weekday in range(5):
            for opens, closes in DEMO_WINDOWS:
                session.add(
                    ScheduleRuleRow(
                        group_id=group.id,
                        weekday=weekday,
                        opens=opens,
                        closes=closes,
                        capacity=DEMO_CAPACITY,
                    )
                )
        session.add(
            ReminderRuleRow(
                group_id=group.id, channel="email", minutes_before=24 * 60, enabled=True
            )
        )
    return True
