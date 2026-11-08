"""Usage facts against SQLite: written in a savepoint, switched off, never failing the caller."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from structlog.testing import capture_logs

from app.adapters.db.models import UsageEventRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, FactKind, StaffFact, UsageFact, UsageOutcome, hour_of

AT = hour_of(datetime(2026, 11, 9, 10, 41, tzinfo=UTC))


def _fact(**extra: object) -> UsageFact:
    return UsageFact(FactKind.BOOKING_CANCELLED, AT, 1, 2, **extra)  # type: ignore[arg-type]


async def _rows(engine: AsyncEngine) -> list[UsageEventRow]:
    async with AsyncSession(engine) as session:
        return list(await session.scalars(select(UsageEventRow).order_by(UsageEventRow.id)))


async def test_a_fact_is_stored_with_its_codes_as_plain_values(engine: AsyncEngine) -> None:
    fact = _fact(
        booking_kind=BookingKind.DIRECT,
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        actor_class=ActorClass.PLANNER,
        outcome=UsageOutcome.CANCELLED_BY_PLANNER,
        reason_code="staffing",
        locale="nl",
        value_seconds=-30,
    )
    async with SqlUnitOfWork(engine).locked() as tx:
        assert await tx.record_usage(fact) is True

    (row,) = await _rows(engine)
    assert (row.kind, row.bucket_hour, row.organisation_id, row.group_id) == (
        "booking.cancelled",
        AT,
        1,
        2,
    )
    assert (row.booking_kind, row.dispatch_kind) == ("direct", "openplanner_queue")
    assert (row.actor_class, row.outcome, row.reason_code) == (
        "planner",
        "cancelled_by_planner",
        "staffing",
    )
    assert (row.locale, row.value_seconds, row.staff_id) == ("nl", -30, None)


async def test_a_staff_fact_keeps_the_team_member_id(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.record_usage(
            StaffFact(FactKind.CALL_ENDED, AT, 1, 2, staff_id=7, value_seconds=60)
        )

    (row,) = await _rows(engine)
    assert (row.staff_id, row.value_seconds) == (7, 60)


async def test_a_fact_rolls_back_with_its_transaction(engine: AsyncEngine) -> None:
    class BoomError(Exception):
        pass

    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.record_usage(_fact())
            raise BoomError
    except BoomError:
        pass

    assert await _rows(engine) == []


async def test_nothing_is_written_with_reporting_switched_off(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine, usage_enabled=lambda: False)
    async with uow.locked() as tx:
        assert await tx.record_usage(_fact()) is False

    assert await _rows(engine) == []


async def test_the_switch_is_read_at_each_write(engine: AsyncEngine) -> None:
    state = {"on": False}
    uow = SqlUnitOfWork(engine, usage_enabled=lambda: state["on"])
    async with uow.locked() as tx:
        await tx.record_usage(_fact())
        state["on"] = True
        await tx.record_usage(_fact())

    assert len(await _rows(engine)) == 1


async def test_a_failing_insert_is_dropped_and_the_transaction_goes_on(
    engine: AsyncEngine,
) -> None:
    """The state change must commit even when the fact cannot be written."""
    with capture_logs() as logs:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx._session.execute(text("DROP TABLE usage_events"))  # noqa: SLF001
            written = await tx.record_usage(_fact(locale="nl"))
            await tx._session.execute(text("CREATE TABLE marker (id INTEGER)"))  # noqa: SLF001

    assert written is False
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(text("marker"))) == 0
    dropped = [entry for entry in logs if entry["event"] == "usage.fact_dropped"]
    assert dropped == [
        {"event": "usage.fact_dropped", "kind": "booking.cancelled", "log_level": "warning"}
    ]
