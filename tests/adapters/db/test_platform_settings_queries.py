"""Storage of portal values and ``settings_version`` (ADR 0042 §4)."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import PlatformSettingRow, SettingsVersionRow
from app.adapters.db.uow import SqlUnitOfWork

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


async def test_a_fresh_database_has_version_zero_and_no_values(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine).read() as tx:
        assert await tx.settings_version() == 0
        assert await tx.platform_settings() == {}


async def test_values_are_read_back_as_plain_json(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        session.add_all(
            [
                PlatformSettingRow(key="admin_session_hours", value="4", updated_at=NOW),
                PlatformSettingRow(key="log_level", value='"DEBUG"', updated_at=NOW),
                PlatformSettingRow(key="database_max_mb", value="null", updated_at=NOW),
                SettingsVersionRow(id=1, version=3),
            ]
        )

    async with SqlUnitOfWork(engine).read() as tx:
        version = await tx.settings_version()
        values = await tx.platform_settings()

    assert version == 3
    assert values == {"admin_session_hours": 4, "log_level": "DEBUG", "database_max_mb": None}


async def test_a_row_that_is_not_json_is_left_out(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        session.add(PlatformSettingRow(key="admin_session_hours", value="{oops", updated_at=NOW))

    async with SqlUnitOfWork(engine).read() as tx:
        assert await tx.platform_settings() == {}
