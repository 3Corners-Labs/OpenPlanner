"""Fixtures shared by the domain tests: a fixed clock, bookings and closures.

``uow`` defaults to the three-group store of ``tests.support.closures``; a test module that
defines its own ``uow`` (or ``clock``, ``bookings``) overrides it.
"""

import pytest

from app.domain.admin import AdminService
from app.domain.closure_service import ClosureService
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
    return FixedClock(NOW)


@pytest.fixture
def bookings(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    return BookingService(uow, clock, TEST_TOKEN_KEY)


@pytest.fixture
def closures(
    uow: InMemoryUnitOfWork, clock: FixedClock, bookings: BookingService
) -> ClosureService:
    return ClosureService(uow, clock, AdminService(uow, clock), bookings)
