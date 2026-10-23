from datetime import UTC, timedelta

from app.adapters.clock import SystemClock
from app.domain.ports import Clock
from tests.fakes.clock import FixedClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is UTC


def test_clocks_satisfy_the_port() -> None:
    clocks: list[Clock] = [SystemClock(), FixedClock()]

    assert all(clock.now().utcoffset() == timedelta(0) for clock in clocks)
