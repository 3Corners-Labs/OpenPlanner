"""Reads of what a booking recorded about its flow (ADR 0121 step 3a), shared by the tests.

The helpers read through the engine of the application under test, so a test can ask what was
stored without going through a page.
"""

from dataclasses import replace

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import BookingRow, GroupRow, ServiceFlowRow, SlotRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.scheduling import Offer


def engine_of(client: httpx.AsyncClient) -> AsyncEngine:
    """Return the database engine of the application a test client talks to."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    engine: AsyncEngine = app.state.engine
    return engine


async def booking_rows(engine: AsyncEngine) -> list[BookingRow]:
    """Return every booking row, oldest first."""
    async with AsyncSession(engine) as session:
        return list((await session.scalars(select(BookingRow).order_by(BookingRow.id))).all())


async def slot_count(engine: AsyncEngine) -> int:
    """Return how many slots exist."""
    async with AsyncSession(engine) as session:
        return len((await session.scalars(select(SlotRow.id))).all())


async def flow_ids(engine: AsyncEngine, group: str = "demo") -> dict[str, int]:
    """Return the id of each flow of the group, by code."""
    async with AsyncSession(engine) as session:
        rows = await session.execute(
            select(ServiceFlowRow.code, ServiceFlowRow.id)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .where(GroupRow.slug == group)
        )
        return {code: flow_id for code, flow_id in rows.all()}


async def flow_columns(engine: AsyncEngine) -> list[tuple[int | None, int | None]]:
    """Return the flow and revision recorded on every booking, oldest first."""
    return [(row.flow_id, row.flow_revision) for row in await booking_rows(engine)]


async def booking_flow(
    engine: AsyncEngine, booking_id: int | None
) -> tuple[int | None, int | None]:
    """Return the flow and revision recorded on one booking."""
    async with AsyncSession(engine) as session:
        row = await session.scalar(select(BookingRow).where(BookingRow.id == booking_id))
    assert row is not None
    return row.flow_id, row.flow_revision


async def offer_direct_calls(engine: AsyncEngine, group_slug: str = "demo") -> None:
    """Let every staffing block of the group offer direct calls and give it its direct flow."""
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group(group_slug)
        assert group is not None
        rules, _overrides = await tx.get_schedule_items(group.id)
        for rule in rules:
            block = replace(rule.window, offer=Offer.BOTH)
            await tx.update_schedule_rule(group.id, rule.id or 0, replace(rule, window=block))
        await tx.ensure_default_flows(group.id)
