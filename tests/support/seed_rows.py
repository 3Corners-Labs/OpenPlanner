"""Rows written straight into a migrated test database, for the adapter, CLI and webhook tests.

``seed_group`` adds an organisation with one group open one hour on one weekday; ``close_day``
closes a day of it; ``count_images`` counts the stored images. The database itself is the
``database_url`` and ``engine`` fixtures of ``tests/adapters/db/conftest.py``, or one of the
test's own.
"""

from datetime import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import (
    ClosureImageRow,
    GroupRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
)


async def seed_group(
    engine: AsyncEngine,
    *,
    slug: str = "balie",
    weekday: int = 0,
    capacity: int = 1,
    phone_mode: str = "optional",
    reminder_minutes: int | None = 24 * 60,
) -> int:
    """Insert an organisation and a group open 09:00-10:00 on ``weekday``; return its ID."""
    async with AsyncSession(engine) as session, session.begin():
        organisation = OrganisationRow(name="Acme", slug=f"org-{slug}")
        session.add(organisation)
        await session.flush()
        group = GroupRow(
            organisation_id=organisation.id,
            name=slug.title(),
            slug=slug,
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode=phone_mode,
        )
        session.add(group)
        await session.flush()
        session.add(
            ScheduleRuleRow(
                group_id=group.id,
                weekday=weekday,
                opens=time(9),
                closes=time(10),
                capacity=capacity,
            )
        )
        if reminder_minutes is not None:
            session.add(
                ReminderRuleRow(
                    group_id=group.id,
                    channel="email",
                    minutes_before=reminder_minutes,
                    enabled=True,
                )
            )
        return group.id


async def close_day(engine: AsyncEngine, group_id: int, day: object) -> None:
    """Close ``day`` for the group with ``group_id`` (a schedule override without hours)."""
    async with AsyncSession(engine) as session, session.begin():
        session.add(ScheduleOverrideRow(group_id=group_id, day=day))


async def count_images(engine: AsyncEngine) -> int:
    """Return how many image rows there are (closure and scenario images, ADR 0037)."""
    async with engine.connect() as connection:
        result = await connection.execute(select(func.count()).select_from(ClosureImageRow))
        return int(result.scalar_one())
