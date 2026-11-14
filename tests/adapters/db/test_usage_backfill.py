"""The usage backfill against SQLite: from surviving columns, once, never twice (ADR 0117)."""

from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import BookingRow, UsageBackfillRow, UsageEventRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.db.usage_rollup import BACKFILL_COLUMNS
from app.domain.privacy import PURGED_FIELDS
from app.domain.services import BookingService
from app.domain.usage import FactKind
from app.domain.usage_backfill_service import UsageBackfill
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group


async def _facts(engine: AsyncEngine) -> list[UsageEventRow]:
    async with AsyncSession(engine) as session:
        return list(await session.scalars(select(UsageEventRow).order_by(UsageEventRow.id)))


async def _old_bookings(engine: AsyncEngine, count: int = 2) -> None:
    """Book ``count`` slots, then forget the facts live code wrote: they predate reporting."""
    await seed_group(engine, capacity=3)
    service = BookingService(
        SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0
    )
    for start in (SLOT_0900, SLOT_0930)[:count]:
        await book(service, start)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(UsageEventRow.__table__.delete())  # type: ignore[attr-defined]


def _backfill(
    engine: AsyncEngine, clock: FixedClock | None = None, *, on: bool = True
) -> UsageBackfill:
    return UsageBackfill(
        SqlUnitOfWork(engine), clock or FixedClock(NOW + timedelta(days=30)), lambda: on
    )


async def test_the_columns_it_reads_are_none_of_the_purged_ones() -> None:
    assert not set(BACKFILL_COLUMNS) & set(PURGED_FIELDS)
    assert not {c for c in BACKFILL_COLUMNS if not hasattr(BookingRow, c)}


async def test_old_bookings_become_facts_by_what_survives(engine: AsyncEngine) -> None:
    await _old_bookings(engine)

    run = await _backfill(engine).run()

    assert run.bookings == 2
    kinds = [f.kind for f in await _facts(engine)]
    assert kinds.count(FactKind.BOOKING_CREATED.value) == 2  # type: ignore[arg-type]
    assert kinds.count(FactKind.BOOKING_SLOTTED.value) == 2  # type: ignore[arg-type]
    assert kinds.count(FactKind.BOOKING_CONFIRMED.value) == 2  # type: ignore[arg-type]
    created = next(f for f in await _facts(engine) if f.kind == "booking.created")
    assert (created.locale, created.dispatch_kind, created.booking_kind) == (
        "nl",
        "contact_center",
        "scheduled",
    )
    assert created.value_seconds is not None
    assert created.value_seconds % 900 == 0


async def test_a_second_run_does_nothing(engine: AsyncEngine) -> None:
    await _old_bookings(engine)
    backfill = _backfill(engine)
    await backfill.run()
    written = len(await _facts(engine))

    again = await backfill.run()

    assert (again.bookings, again.facts) == (0, 0)
    assert len(await _facts(engine)) == written
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageBackfillRow)) == 2


async def test_the_automatic_first_run_happens_once(engine: AsyncEngine) -> None:
    await _old_bookings(engine)
    backfill = _backfill(engine)

    first = await backfill.run_once()
    second = await backfill.run_once()

    assert first.bookings == 2
    assert second.bookings == 0
    async with SqlUnitOfWork(engine).read() as tx:
        assert (await tx.usage_state()).backfilled_at is not None


async def test_a_booking_after_the_cutoff_is_left_to_live_facts(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=3)
    clock = FixedClock(NOW)
    service = BookingService(SqlUnitOfWork(engine), clock, TEST_TOKEN_KEY, treatment_buffer=0)
    await book(service, SLOT_0900)  # live facts exist from here on
    live = len(await _facts(engine))

    run = await UsageBackfill(SqlUnitOfWork(engine), clock, lambda: True).run()

    assert run.facts == 0
    assert len(await _facts(engine)) == live


async def test_nothing_is_written_or_marked_with_reporting_off(engine: AsyncEngine) -> None:
    await _old_bookings(engine)

    run = await _backfill(engine, on=False).run()

    assert run.skipped is True
    assert await _facts(engine) == []
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageBackfillRow)) == 0


async def test_a_cancelled_old_booking_is_a_cancellation_by_the_participant(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, capacity=3)
    clock = FixedClock(NOW)
    service = BookingService(SqlUnitOfWork(engine), clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book(service, SLOT_0900)
    await service.cancel(receipt.management_token)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(UsageEventRow.__table__.delete())  # type: ignore[attr-defined]

    await _backfill(engine).run()

    cancelled = [f for f in await _facts(engine) if f.kind == "booking.cancelled"]
    assert [(f.outcome, f.actor_class) for f in cancelled] == [
        ("cancelled_by_participant", "participant")
    ]


async def test_a_moved_old_booking_is_one_move_and_the_new_one_no_new_booking(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, capacity=3)
    service = BookingService(
        SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0
    )
    receipt = await book(service, SLOT_0900)
    await service.reschedule(receipt.management_token, SLOT_0930)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(UsageEventRow.__table__.delete())  # type: ignore[attr-defined]

    await _backfill(engine).run()

    facts = await _facts(engine)
    assert [f.kind for f in facts].count("booking.moved") == 1
    assert "booking.cancelled" not in [f.kind for f in facts]
    created = [f for f in facts if f.kind == "booking.created"]
    assert sorted(f.reason_code or "" for f in created) == ["", "move"]


async def test_the_backfill_never_selects_a_purged_column(engine: AsyncEngine) -> None:
    """Whatever the rows hold, the statement names only ``BACKFILL_COLUMNS`` of the booking."""
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, *rest):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202, ARG001
        statements.append(statement)

    await _old_bookings(engine, 1)
    statements.clear()
    await _backfill(engine).run()

    reads = [
        s for s in statements if "FROM bookings" in s and s.lstrip().upper().startswith("SELECT")
    ]
    assert reads
    for statement in reads:
        select_list = statement.split(" FROM ")[0]
        for name in PURGED_FIELDS:
            assert f"bookings.{name}" not in select_list, name


async def test_the_worker_backfills_in_bounded_passes_until_complete(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import usage_backfill_service

    await _old_bookings(engine)
    monkeypatch.setattr(usage_backfill_service, "BATCH_SIZE", 1)
    monkeypatch.setattr(usage_backfill_service, "MAX_BATCHES", 1)
    backfill = _backfill(engine)

    first = await backfill.run_once()
    second = await backfill.run_once()
    third = await backfill.run_once()
    fourth = await backfill.run_once()

    assert [r.complete for r in (first, second, third, fourth)] == [False, False, True, True]
    assert (first.bookings, second.bookings) == (1, 1)
    async with SqlUnitOfWork(engine).read() as tx:
        assert (await tx.usage_state()).backfilled_at is not None
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageBackfillRow)) == 2
