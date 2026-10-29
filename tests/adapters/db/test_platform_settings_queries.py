"""Storage of portal values and ``settings_version`` (ADR 0042 §4)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import OrganisationRow, PlatformSettingRow, SettingsVersionRow
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


async def test_a_value_is_saved_replaced_and_deleted_with_the_version(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    later = NOW + timedelta(hours=2)
    async with uow.locked() as tx:
        await tx.save_platform_setting("admin_session_hours", 4, NOW, None)
        await tx.save_platform_setting("log_level", "DEBUG", NOW, later)
        assert await tx.bump_settings_version() == 1
    async with uow.locked() as tx:
        await tx.save_platform_setting("admin_session_hours", 3, NOW, None)
        assert await tx.bump_settings_version() == 2

    async with uow.read() as tx:
        assert await tx.platform_settings() == {"admin_session_hours": 3, "log_level": "DEBUG"}
        assert await tx.platform_setting_expiries() == {"log_level": later}
        assert await tx.settings_version() == 2

    async with uow.locked() as tx:
        assert await tx.delete_platform_setting("log_level")
        assert not await tx.delete_platform_setting("log_level")
    async with uow.read() as tx:
        assert await tx.platform_setting_expiries() == {}


async def test_organisation_values_are_kept_per_organisation(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        first = OrganisationRow(name="Eerste", slug="eerste")
        other = OrganisationRow(name="Andere", slug="andere")
        session.add_all([first, other])
        await session.flush()
        first_id, other_id = first.id, other.id
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_organisation_setting(first_id, "time_format", "12h", NOW)
        await tx.save_organisation_setting(other_id, "shortfall_horizon_days", 30, NOW)
        await tx.save_organisation_setting(first_id, "time_format", "locale", NOW)

    async with uow.read() as tx:
        values = await tx.organisation_settings()
    assert values == {first_id: {"time_format": "locale"}, other_id: {"shortfall_horizon_days": 30}}

    async with uow.locked() as tx:
        assert await tx.delete_organisation_setting(first_id, "time_format")
        assert not await tx.delete_organisation_setting(first_id, "time_format")
    async with uow.read() as tx:
        assert await tx.organisation_settings() == {other_id: {"shortfall_horizon_days": 30}}
