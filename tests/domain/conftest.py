"""Fixtures shared by the closure use-case tests; each module provides its own ``uow``."""

import pytest

from app.domain.admin import AdminService
from app.domain.closure_service import ClosureService
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW, TEST_TOKEN_KEY


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
