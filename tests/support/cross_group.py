"""Shared checks for the tests that try another group's booking through one's own group.

A booking id under another group's address answers ``booking_not_found``, the same as an
unknown id (ADR 0014, ``app.domain.lookups.group_booking``). Each service that acts on one
booking has a test that tries every such use case that way: ``booking_id_methods`` lists the
use cases it must cover, and ``refused_as_unknown`` is its assertion.
"""

import inspect
from collections.abc import Awaitable

import pytest

from app.domain.errors import DomainError, ErrorCode
from tests.fakes.persistence import InMemoryUnitOfWork


def booking_id_methods(service: type) -> set[str]:
    """Return the public use cases of ``service`` that act on one booking (``booking_id``).

    A test that tries each of them with another group's booking compares its list with this
    one, so a use case added later is tried too.
    """
    return {
        name
        for name, method in inspect.getmembers(service, inspect.iscoroutinefunction)
        if not name.startswith("_") and "booking_id" in inspect.signature(method).parameters
    }


async def refused_as_unknown(
    uow: InMemoryUnitOfWork, booking_id: int, attempt: Awaitable[object]
) -> None:
    """Assert that ``attempt`` answers ``booking_not_found`` and leaves everything as it was.

    The error code is what catches a missing group check, and one made after the use case's
    own rule: either would act or answer another code. The booking and the audit log are
    compared as well, but they only show a change saved outside the refused call's
    transaction (such as a refusal audited on its own): the fake keeps a transaction's writes
    only when it succeeds.
    """
    before = uow.state.bookings[booking_id]
    audited = len(uow.state.audit)

    with pytest.raises(DomainError) as refused:
        await attempt

    assert refused.value.code is ErrorCode.BOOKING_NOT_FOUND
    assert uow.state.bookings[booking_id] == before
    assert len(uow.state.audit) == audited
