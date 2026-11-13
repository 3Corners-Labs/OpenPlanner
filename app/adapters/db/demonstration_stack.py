"""The demonstration fixture on a development database (ADR 0121 gate item G06).

``python -m app.cli seed-demonstration`` runs ``seed_demonstration`` on the service ``demo``
after ``seed-demo``, so the e2e stack and a developer's own stack carry the fixture's pools and
flows. Three things differ from the fixture of the tests, each for the browser checks of the
immediate pages, which run at whatever hour the suite starts:

* The document review also takes work without an appointment: its second revision adds the
  on-demand path, so ``/g/demo/direct?flow=documentcontrole`` is the immediate form.
* Its own pool is staffed around the clock with two units (00:00 to 23:55 every day; a block
  cannot end at 24:00), so the form takes a request whenever the suite runs, and a request the
  set-up claims leaves a unit for the form's forecast. The pool's time zone is the first of
  ``ZONES`` that is not near midnight at seeding, so the closed five minutes stay out of a run.
* The service keeps its subjects as they are (``subjects=False``): every participant spec books
  on ``demo``, and a subject is the required first question of each of its forms.

Nothing here opens the rollback gate: while ``ROLLBACK_SAFE`` is false the flows stay dark to
participants (the immediate form is refused, the direct call of ``demo`` is unchanged) and only
the admin pages show them. Development and test only; the command refuses production, like
``seed-closures``. Idempotent. Configuration only, never a participant.
"""

from dataclasses import replace
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.demonstration import (
    EVERYTHING,
    REVIEW_FLOW,
    Demonstration,
    seed_demonstration,
)
from app.adapters.db.seed import DEMO_SLUG, seeded_group
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.ports import Transaction
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow
from app.domain.service_flows import IntakeMode

IMMEDIATE_FORM = f"/g/{DEMO_SLUG}/direct?flow={REVIEW_FLOW}"
"""The immediate form of the document review on the stack (``e2e/pages.json``)."""
ZONES = ("Europe/Amsterdam", "Asia/Tokyo")
"""Candidate time zones of the review pool, eight hours apart: one is never near midnight."""
STACK_UNITS = 2
"""Units of the review pool on the stack: one for the set-up's claim, one for the forecast."""
OPENS = time(0)
CLOSES = time(23, 55)
"""The stack's block of every day; a block cannot end at 24:00."""


async def seed_demonstration_stack(engine: AsyncEngine, now: datetime) -> Demonstration:
    """Store the fixture on ``demo`` with the stack's three differences; return its ids.

    Needs ``seed_demo`` first.

    Args:
        engine: The development database.
        now: The moment of seeding (UTC); it chooses the review pool's time zone.
    """
    async with SqlUnitOfWork(engine).locked() as tx:
        demo = await seeded_group(tx, DEMO_SLUG)
        ids = await seed_demonstration(tx, demo, None, subjects=False)
        await _on_demand(tx, ids.flows[REVIEW_FLOW], now)
        await _around_the_clock(tx, ids.own_pool, away_from_midnight(now))
    return ids


def away_from_midnight(now: datetime) -> str:
    """Return the first of ``ZONES`` whose local hour at ``now`` is from 01:00 to before 22:00.

    The suite runs well under two hours after seeding, so the closed 23:55 to 00:00 of the pool
    in that zone never falls inside it.
    """
    for name in ZONES:
        if 1 <= now.astimezone(ZoneInfo(name)).hour < 22:
            return name
    return ZONES[0]  # pragma: no cover - eight hours apart, one of the two always qualifies


async def _on_demand(tx: Transaction, flow_id: int, now: datetime) -> None:
    """Publish a revision of the flow that also takes work without an appointment, once."""
    flow = await tx.get_service_flow(EVERYTHING, flow_id)
    if flow is None or flow.current is None:  # pragma: no cover - the fixture just stored it
        raise ValueError("the document review has no published revision")
    current = flow.current
    if IntakeMode.ON_DEMAND in current.intake_modes:
        return
    draft = replace(
        current,
        revision=current.revision + 1,
        intake_modes=current.intake_modes | {IntakeMode.ON_DEMAND},
        published_at=None,
        published_by=None,
    )
    await tx.save_draft(EVERYTHING, replace(flow, draft=draft))
    await tx.publish_draft(EVERYTHING, flow_id, by=None, at=now)


async def _around_the_clock(tx: Transaction, pool_id: int, timezone: str) -> None:
    """Staff the pool every day from ``OPENS`` to ``CLOSES`` with ``STACK_UNITS`` in ``timezone``.

    A day's existing block is replaced, a missing day added; the time zone is set every run.
    """
    await tx.set_pool_timezone(EVERYTHING, pool_id, timezone)
    staffing = await tx.get_pool_staffing(EVERYTHING, pool_id)
    existing = {rule.weekday: rule.id for rule in staffing.rules} if staffing else {}
    window = TimeWindow(OPENS, CLOSES, STACK_UNITS, None, Offer.BOTH)
    for weekday in range(7):
        rule = ScheduleRule(weekday, window)
        rule_id = existing.get(weekday)
        if rule_id is None:
            await tx.add_pool_rule(EVERYTHING, pool_id, rule)
        else:
            await tx.update_pool_rule(EVERYTHING, pool_id, rule_id, rule)
