"""Default fixtures of the domain tests: the three-group store, a fixed clock, bookings.

A test module that defines its own ``uow``, ``clock`` or ``bookings`` overrides these.
"""

import pytest

from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import three_groups
from tests.support.domain import NOW, TEST_TOKEN_KEY


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    """Return an in-memory store with the three groups."""
    return InMemoryUnitOfWork(three_groups())


@pytest.fixture
def clock() -> FixedClock:
    """Return a clock fixed at ``NOW``."""
    return FixedClock(NOW)


@pytest.fixture
def bookings(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    """Return the citizens' booking service over the store."""
    return BookingService(uow, clock, TEST_TOKEN_KEY)
