"""The usage rollup against SQLite: cells and watermark together, idempotent, prunes safely."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import UsageEventRow, UsageHourRow, UsageRollupStateRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.usage import ActorClass, FactKind, UsageFact, UsageOutcome, hour_of
from app.domain.usage_rollup import UsageState, is_lagging
from app.domain.usage_rollup_service import UsageRollup
from tests.fakes.clock import FixedClock

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
HOUR = hour_of(NOW - timedelta(days=1))
INTERVAL = timedelta(minutes=15)


def _rollup(
    engine: AsyncEngine, clock: FixedClock, *, days: int = 35, months: int = 25
) -> UsageRollup:
    return UsageRollup(SqlUnitOfWork(engine), clock, lambda: INTERVAL, lambda: days, lambda: months)


async def _write(engine: AsyncEngine, *facts: UsageFact) -> None:
    async with SqlUnitOfWork(engine).locked() as tx:
        for fact in facts:
            await tx.record_usage(fact)


def _created(at: datetime = HOUR, **extra: object) -> UsageFact:
    return UsageFact(FactKind.BOOKING_CREATED, at, 1, 2, locale="nl", value_seconds=3600, **extra)  # type: ignore[arg-type]


async def _cells(engine: AsyncEngine) -> list[UsageHourRow]:
    async with AsyncSession(engine) as session:
        rows = await session.scalars(select(UsageHourRow).order_by(UsageHourRow.metric))
        return list(rows)


async def _state(engine: AsyncEngine) -> UsageState:
    async with SqlUnitOfWork(engine).read() as tx:
        return await tx.usage_state()


async def test_facts_become_hourly_cells_and_the_watermark_moves_past_them(
    engine: AsyncEngine,
) -> None:
    await _write(engine, _created(), _created())
    clock = FixedClock(NOW)

    run = await _rollup(engine, clock).run_if_due()

    assert run is not None
    assert (run.facts, run.cells) == (2, 4)
    by_metric = {c.metric: c for c in await _cells(engine)}
    assert sorted(by_metric) == ["M2", "Q1", "U1", "U6"]
    assert by_metric["U1"].count == 2
    assert (by_metric["U6"].sum_value, by_metric["U6"].max_value) == (7200, 3600)
    assert by_metric["M2"].locale == "nl"
    state = await _state(engine)
    assert (state.pending, state.rolled_at) == (0, NOW)


async def test_a_second_run_adds_nothing(engine: AsyncEngine) -> None:
    await _write(engine, _created())
    clock = FixedClock(NOW)
    rollup = _rollup(engine, clock)
    await rollup.run_if_due()
    before = [(c.metric, c.count) for c in await _cells(engine)]

    clock.advance(INTERVAL)
    again = await rollup.run_if_due()

    assert again is not None
    assert again.facts == 0
    assert [(c.metric, c.count) for c in await _cells(engine)] == before


async def test_a_later_fact_adds_to_the_row_of_its_hour_and_cut(engine: AsyncEngine) -> None:
    clock = FixedClock(NOW)
    rollup = _rollup(engine, clock)
    await _write(engine, _created())
    await rollup.run_if_due()
    await _write(engine, _created(), _created(at=HOUR + timedelta(hours=1)))
    clock.advance(INTERVAL)

    await rollup.run_if_due()

    u1 = [c for c in await _cells(engine) if c.metric == "U1"]
    assert sorted((c.bucket_hour, c.count) for c in u1) == [
        (HOUR, 2),
        (HOUR + timedelta(hours=1), 1),
    ]


async def test_the_cut_columns_keep_apart_cells_of_the_same_metric(engine: AsyncEngine) -> None:
    await _write(
        engine,
        UsageFact(
            FactKind.BOOKING_CANCELLED, HOUR, 1, 2, outcome=UsageOutcome.CANCELLED_BY_PLANNER
        ),
        UsageFact(
            FactKind.BOOKING_CANCELLED,
            HOUR,
            1,
            2,
            outcome=UsageOutcome.CANCELLED_BY_SYSTEM,
            actor_class=ActorClass.SYSTEM,
        ),
    )

    await _rollup(engine, FixedClock(NOW)).run_if_due()

    outcomes = sorted(c.outcome or "" for c in await _cells(engine) if c.metric == "U2")
    assert outcomes == ["cancelled_by_planner", "cancelled_by_system"]


async def test_the_run_waits_for_its_interval(engine: AsyncEngine) -> None:
    clock = FixedClock(NOW)
    rollup = _rollup(engine, clock)

    assert await rollup.run_if_due() is not None
    clock.advance(INTERVAL - timedelta(seconds=1))
    assert await rollup.run_if_due() is None
    clock.advance(timedelta(seconds=1))
    assert await rollup.run_if_due() is not None


async def test_cells_and_watermark_roll_back_together(engine: AsyncEngine) -> None:
    """A crash between the cells and the watermark loses nothing and double-counts nothing."""

    class CrashError(Exception):
        pass

    await _write(engine, _created())
    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            batch = await tx.read_usage_events(0, 10)
            from app.domain.usage_rollup import roll

            await tx.add_usage_cells(roll(batch.facts))
            raise CrashError
    except CrashError:
        pass

    assert await _cells(engine) == []
    assert (await _state(engine)).pending == 1
    await _rollup(engine, FixedClock(NOW)).run_if_due()
    assert [c.count for c in await _cells(engine) if c.metric == "U1"] == [1]


async def test_a_row_this_version_cannot_read_is_passed_over(engine: AsyncEngine) -> None:
    await _write(engine, _created())
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(update(UsageEventRow).values(kind="booking.from_the_future"))
    await _write(engine, _created())

    run = await _rollup(engine, FixedClock(NOW)).run_if_due()

    assert run is not None
    assert (run.facts, run.unreadable) == (1, 1)
    assert (await _state(engine)).pending == 0


async def test_old_rolled_up_facts_and_old_hours_are_pruned(engine: AsyncEngine) -> None:
    old = hour_of(NOW - timedelta(days=40))
    ancient = hour_of(NOW - timedelta(days=800))
    await _write(engine, _created(at=old), _created(at=ancient), _created())
    clock = FixedClock(NOW)

    run = await _rollup(engine, clock, days=35, months=25).run_if_due()

    assert run is not None
    assert (run.pruned.events, run.pruned.hours) == (2, 4)
    async with AsyncSession(engine) as session:
        left = (await session.scalars(select(UsageEventRow.bucket_hour))).all()
    assert [h.replace(tzinfo=UTC) for h in left] == [HOUR]
    assert {c.bucket_hour for c in await _cells(engine)} == {old, HOUR}


async def test_a_fact_the_rollup_has_not_read_is_never_pruned(engine: AsyncEngine) -> None:
    await _write(engine, _created(at=hour_of(NOW - timedelta(days=60))))
    async with SqlUnitOfWork(engine).locked() as tx:
        pruned = await tx.prune_usage(NOW, NOW - timedelta(days=900), 100)

    assert pruned.events == 0
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageEventRow)) == 1


@pytest.mark.parametrize(
    ("pending", "ago", "lagging"),
    [
        (0, timedelta(days=3), False),
        (5, timedelta(minutes=59), False),
        (5, timedelta(minutes=61), True),
        (5, None, True),
    ],
)
def test_the_rollup_lags_when_facts_wait_four_intervals(
    pending: int, ago: timedelta | None, lagging: bool
) -> None:
    state = UsageState(0, None if ago is None else NOW - ago, pending)

    assert is_lagging(state, NOW, INTERVAL) is lagging


async def test_the_state_row_is_one_row(engine: AsyncEngine) -> None:
    clock = FixedClock(NOW)
    rollup = _rollup(engine, clock)
    for _ in range(3):
        await rollup.run_if_due()
        clock.advance(INTERVAL)

    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageRollupStateRow)) == 1


async def test_an_id_is_never_given_out_twice(engine: AsyncEngine) -> None:
    """Pruned facts must not free their ids: a new fact below the watermark would be lost."""
    old = hour_of(NOW - timedelta(days=60))
    await _write(engine, _created(at=old), _created(at=old))
    await _rollup(engine, FixedClock(NOW)).run_if_due()
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(UsageEventRow)) == 0

    await _write(engine, _created())

    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.min(UsageEventRow.id))) == 3
    assert (await _state(engine)).pending == 1


async def test_retention_deletes_in_bounded_batches(engine: AsyncEngine) -> None:
    old = hour_of(NOW - timedelta(days=60))
    await _write(engine, *(_created(at=old) for _ in range(5)))
    async with SqlUnitOfWork(engine).locked() as tx:
        batch = await tx.read_usage_events(0, 100)
        await tx.advance_usage_watermark(batch.last_id, NOW)

    counts = []
    for _ in range(3):
        async with SqlUnitOfWork(engine).locked() as tx:
            counts.append((await tx.prune_usage(NOW, NOW - timedelta(days=900), 2)).events)

    assert counts == [2, 2, 1]


async def test_a_run_stops_after_its_batches_and_the_next_pass_goes_on(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import usage_rollup_service

    monkeypatch.setattr(usage_rollup_service, "BATCH_SIZE", 2)
    monkeypatch.setattr(usage_rollup_service, "MAX_BATCHES", 1)
    await _write(engine, *(_created() for _ in range(5)))
    rollup = _rollup(engine, FixedClock(NOW))

    first = await rollup.run_if_due()
    second = await rollup.run_if_due()  # not due by the clock, but work was left
    third = await rollup.run_if_due()

    assert first is not None
    assert second is not None
    assert third is not None
    assert (first.facts, first.more) == (2, True)
    assert (second.facts, second.more) == (2, True)
    assert (third.facts, third.more) == (1, False)
    assert (await _state(engine)).pending == 0
    assert await rollup.run_if_due() is None


async def test_a_slot_keyed_fact_says_nothing_about_when_it_was_written(
    engine: AsyncEngine,
) -> None:
    future = hour_of(NOW + timedelta(days=30))
    await _write(
        engine,
        UsageFact(
            FactKind.BOOKING_CANCELLED, future, 1, 2, outcome=UsageOutcome.CANCELLED_BY_PLANNER
        ),
    )

    async with SqlUnitOfWork(engine).locked() as tx:
        cutoff = await tx.usage_cutoff(NOW)

    assert cutoff == hour_of(NOW)


async def test_the_cutoff_is_the_first_fact_written_when_there_is_one(
    engine: AsyncEngine,
) -> None:
    early = hour_of(NOW - timedelta(days=3))
    await _write(engine, _created(at=early), _created())

    async with SqlUnitOfWork(engine).locked() as tx:
        first = await tx.usage_cutoff(NOW)
    await _write(engine, _created(at=hour_of(NOW - timedelta(days=9))))
    async with SqlUnitOfWork(engine).locked() as tx:
        again = await tx.usage_cutoff(NOW)

    assert first == early
    assert again == early
