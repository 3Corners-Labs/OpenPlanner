"""The demonstration fixture on a development database (ADR 0121 gate item G06).

``seed-demonstration`` puts the fixture on ``demo`` for the immediate pages of the e2e stack.
With the gate shut the participant sees nothing new; opened by a monkeypatch, the immediate
form of the document review takes a request at 09:00 on a Thursday, which the fixture's own
staffing (weekday afternoons) would not.
"""

import re
from datetime import UTC, datetime, time

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.demonstration import EVERYTHING, IN_PERSON_FLOW, REVIEW_FLOW
from app.adapters.db.demonstration_stack import (
    IMMEDIATE_FORM,
    STACK_UNITS,
    away_from_midnight,
    seed_demonstration_stack,
)
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.service_flows import FlowState, IntakeMode
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import ADA, SAME_ORIGIN
from tests.support.pool_flows import open_gate, shut_gate


async def test_the_review_takes_work_without_appointment_on_a_pool_open_all_day(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)
    first = await seed_demonstration_stack(engine, NOW)
    again = await seed_demonstration_stack(engine, NOW)

    async with SqlUnitOfWork(engine).read() as tx:
        demo = await tx.get_group("demo")
        assert demo is not None
        flows = {f.code: f for f in await tx.list_service_flows(EVERYTHING, demo.id)}
        staffing = await tx.get_pool_staffing(EVERYTHING, first.own_pool)
    review = flows[REVIEW_FLOW].current
    assert review is not None
    assert again == first
    assert flows[REVIEW_FLOW].state is FlowState.ACTIVE
    assert flows[IN_PERSON_FLOW].state is FlowState.ACTIVE
    assert review.revision == 2  # a rerun publishes nothing new
    assert review.intake_modes == {IntakeMode.APPOINTMENT, IntakeMode.ON_DEMAND}
    assert demo.routing.subjects == ()  # every participant spec books on demo
    assert staffing is not None
    assert str(staffing.timezone) == "Europe/Amsterdam"
    assert sorted(r.weekday for r in staffing.rules) == list(range(7))
    for rule in staffing.rules:
        assert (rule.window.opens, rule.window.closes) == (time(0), time(23, 55))
        assert rule.window.capacity == STACK_UNITS


def test_the_pool_zone_is_one_that_is_not_near_midnight() -> None:
    late = datetime(2026, 1, 1, 21, 30, tzinfo=UTC)  # 22:30 in Amsterdam, 06:30 in Tokyo

    assert away_from_midnight(NOW) == "Europe/Amsterdam"
    assert away_from_midnight(late) == "Asia/Tokyo"


async def test_participants_see_it_only_with_the_gate_open(
    settings: Settings, database_url: str, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_demo(engine)
    await seed_demonstration_stack(engine, NOW)
    stack = settings.model_copy(update={"database_url": database_url})

    shut_gate(monkeypatch)
    async with app_client(stack, FixedClock(NOW)) as site:
        shut = await site.get(IMMEDIATE_FORM)
        direct = await site.get("/g/demo/direct")
        booking = await site.get("/g/demo")
        open_gate(monkeypatch)
        form = await site.get(IMMEDIATE_FORM)
        key = re.search(r'name="intake_key" value="([^"]+)"', form.text)
        assert key is not None
        data = {**ADA, "flow": REVIEW_FLOW, "intake_key": key.group(1)}
        sent = await site.post("/g/demo/direct", data=data, headers=SAME_ORIGIN)
        shown = await site.get(sent.headers["location"])

    assert shut.status_code == 422
    assert 'name="intake_key"' not in direct.text  # the legacy direct call of demo stays
    assert 'name="subject"' not in booking.text
    assert form.status_code == 200
    assert sent.status_code == 303
    assert sent.headers["location"].startswith("/r/")
    assert shown.status_code == 200
    assert "data-poll" in shown.text  # the request page polls: an island in e2e/pages.json
