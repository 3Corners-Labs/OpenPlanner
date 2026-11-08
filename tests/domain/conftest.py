"""Fixtures shared by the domain tests: a fixed clock, bookings, closures and identity providers.

``uow`` defaults to the three-group store of ``tests.support.closures``; a test module that
defines its own ``uow`` (or ``clock``, ``bookings``) overrides it. ``discovery``, ``keys`` and
``changed`` serve the identity provider service (ADR 0083).
"""

import pytest

from app.domain.admin import AdminService
from app.domain.closure_service import ClosureService
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.identity_providers import (
    FakeDiscoveryFetcher,
    FakeSigningKeys,
    discovery_document,
)
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import three_groups
from tests.support.domain import NOW, TEST_TOKEN_KEY
from tests.support.organisation_providers import OTHER_DISCOVERY, OTHER_ISSUER


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


@pytest.fixture
def discovery() -> FakeDiscoveryFetcher:
    """The discovery documents the identity provider service reads, ``OTHER_ISSUER``'s too."""
    fetcher = FakeDiscoveryFetcher()
    fetcher.documents[OTHER_DISCOVERY] = discovery_document(OTHER_ISSUER)
    return fetcher


@pytest.fixture
def keys() -> FakeSigningKeys:
    """The signing keys the identity provider service makes for a provider."""
    return FakeSigningKeys()


@pytest.fixture
def changed() -> list[str]:
    """The provider keys whose registration changed, as the service reports them."""
    return []
