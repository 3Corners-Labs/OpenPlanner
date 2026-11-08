"""A committed usage fact becomes the log line usage.fact only when asked (ADR 0117 point 15)."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.usage import FactKind, StaffFact, UsageFact, UsageOutcome, hour_of
from app.logging import MASK, PII_FIELDS, mask_record

AT = hour_of(datetime(2026, 11, 9, 10, 41, tzinfo=UTC))
FACT = UsageFact(
    FactKind.BOOKING_CANCELLED,
    AT,
    1,
    2,
    outcome=UsageOutcome.CANCELLED_BY_PLANNER,
    value_seconds=90,
)


async def test_with_the_setting_on_a_committed_fact_is_one_line_with_a_bucket(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine, usage_to_log=lambda: True)

    with capture_logs() as logs:
        async with uow.locked() as tx:
            await tx.record_usage(FACT)

    lines = [entry for entry in logs if entry["event"] == "usage.fact"]
    assert len(lines) == 1
    assert lines[0]["duration_bucket"] == 1
    assert lines[0]["bucket_hour"] == "2026-11-09T10:00:00Z"
    assert "value_seconds" not in lines[0]


async def test_with_the_setting_off_nothing_is_logged(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)

    with capture_logs() as logs:
        async with uow.locked() as tx:
            await tx.record_usage(FACT)

    assert not [entry for entry in logs if entry["event"] == "usage.fact"]


async def test_a_rolled_back_transaction_logs_nothing(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine, usage_to_log=lambda: True)

    async def failing() -> None:
        async with uow.locked() as tx:
            await tx.record_usage(FACT)
            raise RuntimeError("the change failed")

    with capture_logs() as logs:
        with pytest.raises(RuntimeError):
            await failing()

    assert not [entry for entry in logs if entry["event"] == "usage.fact"]


async def test_a_team_fact_is_never_logged(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine, usage_to_log=lambda: True)

    with capture_logs() as logs:
        async with uow.locked() as tx:
            await tx.record_usage(StaffFact(FactKind.QUEUE_CLAIMED, AT, 1, 2, staff_id=5))

    assert not [entry for entry in logs if entry["event"] == "usage.fact"]


async def test_the_masking_of_the_stream_has_nothing_to_mask_in_a_usage_line(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine, usage_to_log=lambda: True)
    with capture_logs() as logs:
        async with uow.locked() as tx:
            await tx.record_usage(FACT)
    (line,) = [entry for entry in logs if entry["event"] == "usage.fact"]

    masked = mask_record(line)

    assert masked == line
    assert not set(line) & PII_FIELDS
    assert MASK not in json.dumps(masked, default=str)
