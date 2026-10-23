"""A migrated SQLite database per test, plus helpers to seed configuration rows."""

from collections.abc import AsyncIterator
from datetime import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import (
    GroupRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
)
from tests.support.database import migrated_database


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "adapter")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, busy_timeout_ms=200)
    yield engine
    await engine.dispose()


async def seed_group(
    engine: AsyncEngine,
    *,
    slug: str = "aangifte",
    weekday: int = 0,
    capacity: int = 1,
    phone_mode: str = "optional",
    reminder_minutes: int | None = 24 * 60,
) -> int:
    """Insert an organisation and a group open 09:00-10:00 on ``weekday``; return its ID."""
    async with AsyncSession(engine) as session, session.begin():
        organisation = OrganisationRow(name="Gemeente", slug=f"org-{slug}")
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
    async with AsyncSession(engine) as session, session.begin():
        session.add(ScheduleOverrideRow(group_id=group_id, day=day))
