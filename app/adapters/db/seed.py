"""Demo configuration: two organisations with one group each, with weekday opening hours.

``demo`` is the organisation of the development test admins; ``andere`` exists so that every
role can be tested against an organisation and group that are *not* theirs (ADR 0014).
An empty holiday calendar ``nationaal`` is ready for the superuser to fill (ADR 0022); no
organisation has chosen it, so it changes no opening hours.

Used by ``python -m app.cli seed-demo`` and by tests. Idempotent: existing groups are left
untouched.
"""

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import (
    GroupRow,
    HolidayCalendarRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleRuleRow,
)

DEMO_SLUG = "demo"
OTHER_ORGANISATION_SLUG = "andere"
OTHER_GROUP_SLUG = "andere-team"
DEMO_WINDOWS = ((time(9), time(12)), (time(13), time(17)))
DEMO_CAPACITY = 2
DEMO_CALENDAR_SLUG = "nationaal"


async def seed_demo(engine: AsyncEngine) -> bool:
    """Create the demo organisations and groups; return ``False`` if they already exist."""
    created = await _seed_group(engine, DEMO_SLUG, "Demo-gemeente", DEMO_SLUG, "Demo: videogesprek")
    await _seed_group(
        engine, OTHER_ORGANISATION_SLUG, "Andere gemeente", OTHER_GROUP_SLUG, "Andere: team"
    )
    await _seed_calendar(engine)
    return created


async def _seed_calendar(engine: AsyncEngine) -> None:
    """Create the empty demo holiday calendar unless it exists."""
    async with AsyncSession(engine) as session, session.begin():
        existing = await session.scalar(
            select(HolidayCalendarRow.id).where(HolidayCalendarRow.slug == DEMO_CALENDAR_SLUG)
        )
        if existing is None:
            session.add(HolidayCalendarRow(slug=DEMO_CALENDAR_SLUG, name="Nationale feestdagen"))


async def _seed_group(
    engine: AsyncEngine, organisation_slug: str, organisation_name: str, slug: str, name: str
) -> bool:
    """Create one organisation with one group open on weekdays; skip if the group exists."""
    async with AsyncSession(engine) as session, session.begin():
        existing = await session.scalar(select(GroupRow.id).where(GroupRow.slug == slug))
        if existing is not None:
            return False
        organisation = OrganisationRow(name=organisation_name, slug=organisation_slug)
        session.add(organisation)
        await session.flush()
        group = GroupRow(
            organisation_id=organisation.id,
            name=name,
            slug=slug,
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
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
