"""The upgrade fixture of ADR 0116 point 7: what it holds, and the checks the matrix runs."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingRow
from app.adapters.db.upgrade_fixture import (
    FixtureState,
    compare_states,
    inspect_fixture,
    seed_upgrade_fixture,
)
from app.domain.booking import BookingStatus
from tests.support.domain import NOW

KEY = b"k" * 32
LATER = NOW + timedelta(days=400)


def _path(url: str) -> Path:
    return Path(url.removeprefix("sqlite+aiosqlite:///"))


async def test_the_fixture_holds_every_kind_of_row(engine: AsyncEngine, database_url: str) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")

    state = await inspect_fixture(_path(database_url), LATER)

    async with engine.connect() as connection:
        statuses = set(await connection.scalars(select(BookingRow.status).distinct()))
    assert statuses == {s.value for s in BookingStatus}
    assert state.revision
    for name in (
        "organisations",
        "groups",
        "slots",
        "bookings",
        "jobs",
        "audit_events",
        "closures",
        "platform_settings",
        "organisation_settings",
        "identity_providers",
        "api_clients",
        "running_versions",
    ):
        assert state.rows[name] > 0, name
    assert state.purged == 1
    assert state.live_with_data > 0
    assert state.pending_jobs > 0
    assert compare_states(state, state) == []


async def test_inspecting_does_not_change_the_file(engine: AsyncEngine, database_url: str) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")

    first = await inspect_fixture(_path(database_url), LATER)
    second = await inspect_fixture(_path(database_url), LATER)

    assert first == second
    assert first.claimable_jobs == first.pending_jobs


async def test_the_large_fixture_adds_generated_bookings(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234", bookings=2001)

    state = await inspect_fixture(_path(database_url), LATER)

    async with engine.connect() as connection:
        generated = await connection.scalar(
            select(func.count()).where(BookingRow.public_ref.like("fx%"))
        )
    assert generated == 2001
    assert state.purged > 1
    assert state.purged_with_data == 0
    assert state.live_without_data == 0


async def test_a_lost_row_or_personal_field_is_a_finding(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")
    before = await inspect_fixture(_path(database_url), LATER)
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow).where(BookingRow.pii_purged_at.is_(None)).values(email=None)
        )

    after = await inspect_fixture(_path(database_url), LATER)

    findings = compare_states(before, after)
    assert any("live bookings with personal data" in f for f in findings)
    assert any("lost their personal data" in f for f in findings)


def test_findings_name_every_broken_invariant() -> None:
    before = FixtureState("a", {"bookings": 3, "jobs": 2, "gone": 1}, 2, 0, 1, 0, 2, 2)
    after = replace(
        before,
        rows={"bookings": 2, "jobs": 2},
        purged_with_data=1,
        claimable_jobs=1,
        settings_out_of_bounds=["booking_rate_limit"],
    )

    findings = compare_states(before, after)

    assert "table bookings: 3 rows before, 2 after" in findings
    assert "table gone: 1 rows before, 0 after" in findings
    assert "1 purged bookings hold personal data" in findings
    assert "2 pending jobs, but the worker claims 1" in findings
    assert "setting booking_rate_limit is outside its bounds" in findings
    assert FixtureState.from_json(after.to_json()) == after
