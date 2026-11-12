"""A service flow on a staffed counted pool, seeded through the port (ADR 0122 step 5 tests).

``seed_pool_flow`` stores a counted pool with a time zone and weekly blocks and an active flow
of a service that draws on it, through the same ``Transaction`` methods on the SQL adapter and
on the fake, as an image with the rollback gate open would have written them. Activation is
done behind the readiness checklist on purpose: the checklist refuses every new flow while the
gate is shut, and these tests need the state an open gate leads to. ``open_gate`` flips the gate
for one test; the constant itself is never edited.
"""

from dataclasses import dataclass, replace
from datetime import time
from typing import Any

import pytest

from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow
from app.domain.service_flows import FlowState, IntakeMode
from app.domain.staff import StaffMember
from tests.support.domain import NOW
from tests.support.service_flows import flow as make_flow
from tests.support.service_flows import revision

EVERYTHING = Scope(everything=True)
AMSTERDAM = "Europe/Amsterdam"


@dataclass(frozen=True)
class PoolFlow:
    """What ``seed_pool_flow`` stored."""

    pool_id: int
    flow_id: int
    code: str


def open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the rollback gate ``ROLLBACK_SAFE`` for one test."""
    monkeypatch.setattr("app.domain.service_flows.ROLLBACK_SAFE", True)


def block(
    weekday: int = 0,
    opens: str = "09:00",
    closes: str = "12:00",
    units: int = 2,
    offer: Offer = Offer.SCHEDULED,
) -> ScheduleRule:
    """Return a weekly block of a pool: ``units`` people from ``opens`` to ``closes`` (local)."""
    window = TimeWindow(time.fromisoformat(opens), time.fromisoformat(closes), units, None, offer)
    return ScheduleRule(weekday, window)


async def add_counted_pool(
    uow: Any,
    organisation_id: int,
    blocks: list[ScheduleRule],
    *,
    name: str = "Loket",
    timezone: str | None = AMSTERDAM,
) -> int:
    """Store a counted pool of ``organisation_id`` with a zone and ``blocks``; return its id."""
    async with uow.locked() as tx:
        pool = await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(None, organisation_id, name, PoolMode.COUNTED, code=name.lower()),
        )
        if timezone is not None:
            assert await tx.set_pool_timezone(EVERYTHING, pool.id, timezone)
        for rule in blocks:
            assert await tx.add_pool_rule(EVERYTHING, pool.id, rule) is not None
    return int(pool.id)


async def add_pool_flow(
    uow: Any,
    group_id: int,
    pool_id: int,
    *,
    code: str = "advies",
    duration: int = 20,
    default: bool = False,
) -> PoolFlow:
    """Store, publish and activate a telephone flow of ``group_id`` on the pool ``pool_id``."""
    draft = revision(flow_id=None, capacity_pool_id=pool_id, duration_minutes=duration)
    defaults = frozenset({IntakeMode.APPOINTMENT}) if default else frozenset()
    new = replace(make_flow(code, FlowState.DRAFT, None, defaults, None, draft), group_id=group_id)
    async with uow.locked() as tx:
        await tx.save_staff(
            StaffMember(f"seed-{code}", "Seed", frozenset(), frozenset(), NOW, NOW, NOW)
        )
        publisher = await tx.get_staff(f"seed-{code}")
        stored = await tx.save_draft(EVERYTHING, new)
        await tx.publish_draft(EVERYTHING, stored.id, by=publisher.id, at=NOW)
        await tx.set_flow_state(EVERYTHING, stored.id, FlowState.ACTIVE)
    return PoolFlow(pool_id, int(stored.id), code)


async def seed_pool_flow(
    uow: Any,
    organisation_id: int,
    group_id: int,
    blocks: list[ScheduleRule] | None = None,
    **flow: Any,
) -> PoolFlow:
    """Store a staffed counted pool (Mondays 09:00–12:00, 2 units by default) and its flow."""
    pool_id = await add_counted_pool(uow, organisation_id, blocks or [block()])
    return await add_pool_flow(uow, group_id, pool_id, **flow)
