"""The upgrade fixture of ADR 0116 point 7: what it holds, and the checks the matrix runs."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, not_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.db.upgrade_fixture import (
    FixtureState,
    compare_states,
    inspect_fixture,
    seed_upgrade_fixture,
)
from app.domain.booking import BookingStatus
from app.domain.settings_registry import REGISTRY, Kind, SettingClass
from tests.support.domain import NOW

KEY = b"k" * 32
LATER = NOW + timedelta(days=400)
GENERATED_REF = "fx" + "[0-9]" * 10
"""GLOB pattern of ``fx{n:010d}``, the reference of a generated booking (case-sensitive)."""
DECOYS = ("fxAbCdEfGhIjKlMnOpQrSt", "FXaBcDeFgHiJkLmNoPqRsT", "FX0000000000")
"""References of bookings the fixture did not generate, that start like a generated one.

The first two are shaped like a random reference (22 characters), which may begin with ``fx`` in
any case. The last has the generated length and digits in capitals: it is there to prove the
match is case-sensitive, as SQLite's ``LIKE`` is not.
"""


def _is_generated() -> ColumnElement[bool]:
    """Return the condition that a booking's public reference has the generated form."""
    return BookingRow.public_ref.op("GLOB", is_comparison=True)(GENERATED_REF)


async def _give_decoy_references(engine: AsyncEngine) -> None:
    """Store ``DECOYS`` as the public reference of as many bookings the fixture did not generate."""
    async with engine.begin() as connection:
        ids = list(
            await connection.scalars(
                select(BookingRow.id)
                .where(not_(_is_generated()))
                .order_by(BookingRow.id)
                .limit(len(DECOYS))
            )
        )
        assert len(ids) == len(DECOYS)
        for booking_id, ref in zip(ids, DECOYS, strict=True):
            await connection.execute(
                update(BookingRow).where(BookingRow.id == booking_id).values(public_ref=ref)
            )


async def test_the_fixture_holds_every_kind_of_row(engine: AsyncEngine, database_url: str) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")

    state = await inspect_fixture(database_url, LATER)

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

    first = await inspect_fixture(database_url, LATER)
    second = await inspect_fixture(database_url, LATER)

    assert first == second
    assert first.claimable_jobs == first.pending_jobs


async def test_the_large_fixture_adds_generated_bookings(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234", bookings=2001)
    await _give_decoy_references(engine)

    state = await inspect_fixture(database_url, LATER)

    async with engine.connect() as connection:
        generated = await connection.scalar(select(func.count()).where(_is_generated()))
        decoys_stored = await connection.scalar(
            select(func.count()).where(BookingRow.public_ref.in_(DECOYS))
        )
    assert decoys_stored == len(DECOYS)
    assert generated == 2001
    assert state.purged > 1
    assert state.purged_with_data == 0
    assert state.live_without_data == 0


async def test_a_lost_row_or_personal_field_is_a_finding(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")
    before = await inspect_fixture(database_url, LATER)
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow).where(BookingRow.pii_purged_at.is_(None)).values(email=None)
        )

    after = await inspect_fixture(database_url, LATER)

    findings = compare_states(before, after)
    assert any("live bookings with personal data" in f for f in findings)
    assert any("lost their personal data" in f for f in findings)


def test_findings_name_every_broken_invariant() -> None:
    before = FixtureState(
        revision="a",
        rows={"bookings": 3, "jobs": 2, "gone": 1},
        live_with_data=2,
        live_without_data=0,
        purged=1,
        purged_with_data=0,
        pending_jobs=2,
        claimable_jobs=2,
    )
    after = replace(
        before,
        rows={"bookings": 2, "jobs": 2},
        purged_with_data=1,
        claimable_jobs=1,
        settings_unusable=["booking_rate_limit"],
    )

    findings = compare_states(before, after)

    assert "table bookings: 3 rows before, 2 after" in findings
    assert "table gone: 1 rows before, 0 after" in findings
    assert "1 purged bookings hold personal data" in findings
    assert "2 pending jobs, but the worker claims 1" in findings
    assert "setting booking_rate_limit holds a value the code would not apply" in findings
    assert FixtureState.from_json(after.to_json()) == after


async def test_a_missing_file_is_an_error_not_an_empty_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await inspect_fixture(f"sqlite+aiosqlite:///{tmp_path / 'typo.sqlite'}", LATER)

    assert not (tmp_path / "typo.sqlite").exists()


async def test_a_stored_value_of_the_wrong_type_is_a_finding(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_upgrade_fixture(engine, NOW, KEY, version="0.6.0", commit="abc1234")
    spec = next(s for s in REGISTRY if s.class_ is SettingClass.PLATFORM and s.kind is Kind.INT)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.save_platform_setting(spec.key, "not a number", NOW, None)

    state = await inspect_fixture(database_url, LATER)

    assert state.settings_unusable == [spec.key]
