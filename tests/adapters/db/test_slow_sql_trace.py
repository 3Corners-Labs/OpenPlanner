"""The ``sql.slow`` trace point: statement kind and table above a threshold (ADR 0085)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import set_slow_query_ms
from app.adapters.db.models import GroupRow
from tests.support.tracing import captured_trace


async def test_a_statement_above_the_threshold_is_traced_by_kind_and_table(
    engine: AsyncEngine,
) -> None:
    set_slow_query_ms(0)
    try:
        with captured_trace() as lines:
            async with engine.connect() as conn:
                await conn.execute(select(GroupRow).where(GroupRow.slug == "secret-slug"))
    finally:
        set_slow_query_ms(200)

    slow = [line for line in lines if line.point == "sql.slow"]
    assert slow
    assert slow[-1].fields["statement"] == "Select"
    assert slow[-1].fields["table"] == "groups"
    assert isinstance(slow[-1].fields["ms"], int)
    assert "secret-slug" not in repr(lines)


async def test_nothing_is_traced_below_the_threshold(engine: AsyncEngine) -> None:
    set_slow_query_ms(10_000)
    try:
        with captured_trace() as lines:
            async with engine.connect() as conn:
                await conn.execute(select(GroupRow))
    finally:
        set_slow_query_ms(200)

    assert not [line for line in lines if line.point == "sql.slow"]
