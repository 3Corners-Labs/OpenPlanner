"""Maintenance state rules (ADR 0038): switching on, the environment override, overdue."""

from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.maintenance import (
    OFF,
    MaintenanceReason,
    MaintenanceState,
    MaintenanceStatus,
    overdue,
    switched_on,
)
from tests.support.domain import NOW

LATER = NOW + timedelta(hours=2)


def test_switching_on_records_the_start() -> None:
    state = switched_on(NOW, "s-1", MaintenanceReason.UPDATE, LATER, OFF)

    assert state == MaintenanceState(True, NOW, "s-1", LATER, MaintenanceReason.UPDATE)


def test_switching_on_again_keeps_the_start_and_replaces_end_and_reason() -> None:
    first = switched_on(NOW, "s-1", MaintenanceReason.UPDATE, None, OFF)

    again = switched_on(NOW + timedelta(minutes=5), "s-2", MaintenanceReason.INCIDENT, LATER, first)

    assert again == MaintenanceState(True, NOW, "s-1", LATER, MaintenanceReason.INCIDENT)


@pytest.mark.parametrize("end", [NOW, NOW - timedelta(minutes=1)])
def test_an_expected_end_must_be_in_the_future(end: object) -> None:
    with pytest.raises(DomainError) as raised:
        switched_on(NOW, "s-1", MaintenanceReason.OTHER, end, OFF)  # type: ignore[arg-type]

    assert raised.value.code is ErrorCode.INVALID_CONFIGURATION
    assert raised.value.field == "expected_end"


def test_either_source_makes_it_active() -> None:
    on = MaintenanceState(True, NOW, "s-1")

    assert not MaintenanceStatus(OFF).active
    assert MaintenanceStatus(on).active
    assert MaintenanceStatus(OFF, environment=True).active


def test_overdue_only_after_the_limit() -> None:
    status = MaintenanceStatus(MaintenanceState(True, NOW, "s-1"))
    limit = timedelta(hours=4)

    assert not overdue(status, NOW + limit, limit)
    assert overdue(status, NOW + limit + timedelta(seconds=1), limit)


def test_overdue_needs_the_stored_switch() -> None:
    limit = timedelta(hours=4)
    late = NOW + timedelta(days=1)

    assert not overdue(MaintenanceStatus(OFF, environment=True), late, limit)
    assert not overdue(MaintenanceStatus(MaintenanceState(True)), late, limit)
