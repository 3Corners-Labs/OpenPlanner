"""Every event an hourly alert counts is recorded through its check (ADR 0044).

``app.domain.activity_alert`` stays exact only when each successful event it counts is followed
by the alert's check. ``AdminService.record_change`` runs it with the threshold the composition
root gave the service. The module's ``record_change`` and ``AdminService.record`` refuse a
counted success, and ``tests/conftest.py`` fails the run on one built anywhere else.
"""

import sys

import pytest

from app.domain.activity_alert import ALERTS, CHANGES_ALERT, REVEALS_ALERT, alert_for
from app.domain.admin import AdminService, record_change
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission
from tests.conftest import checked_by_its_alert
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support import recorders
from tests.support.admin import GROUP_ADMIN
from tests.support.domain import NOW


async def test_without_its_threshold_a_counted_permission_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    admin = AdminService(uow, clock, alert_thresholds={Permission.BOOKINGS_VIEW: 20})

    with pytest.raises(ValueError, match="planner.changes_alert"):
        async with uow.locked() as tx:
            await admin.record_change(
                tx, NOW, GROUP_ADMIN, Permission.BOOKINGS_MANAGE, "demo", "demo"
            )

    assert uow.state.audit == []


async def test_the_module_function_refuses_a_counted_permission(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(ValueError, match="booking.contact_reveals_alert"):
        async with uow.locked() as tx:
            await record_change(tx, NOW, GROUP_ADMIN, Permission.BOOKINGS_VIEW, "demo", "demo")

    assert uow.state.audit == []


async def test_any_other_permission_is_recorded_without_an_alert(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    async with uow.locked() as tx:
        await record_change(tx, NOW, GROUP_ADMIN, Permission.GROUP_EDIT, "demo", "demo", "name")
        await AdminService(uow, clock).record_change(
            tx, NOW, GROUP_ADMIN, Permission.GROUP_EDIT, "demo", "demo", "slug"
        )

    assert [(e.action, e.detail) for e in uow.state.audit] == [
        ("group.edit", "name"),
        ("group.edit", "slug"),
    ]


def test_a_threshold_for_a_permission_no_alert_counts_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(ValueError, match=r"group\.edit"):
        AdminService(uow, clock, alert_thresholds={Permission.GROUP_EDIT: 5})


async def test_the_threshold_is_read_at_each_change(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    limit = [5]
    admin = AdminService(uow, clock, alert_thresholds={Permission.BOOKINGS_VIEW: lambda: limit[0]})

    async with uow.locked() as tx:
        await admin.record_change(tx, NOW, GROUP_ADMIN, Permission.BOOKINGS_VIEW, "demo", "demo")
        limit[0] = 1
        await admin.record_change(tx, NOW, GROUP_ADMIN, Permission.BOOKINGS_VIEW, "demo", "demo")

    alerts = [e for e in uow.state.audit if e.action == REVEALS_ALERT.action]
    assert [e.detail for e in alerts] == ["actor:g-1,reveals:2"]


def test_each_permission_has_at_most_one_alert() -> None:
    assert len({a.permission for a in ALERTS}) == len(ALERTS)
    assert [alert_for(a.permission) for a in ALERTS] == list(ALERTS)
    assert alert_for(Permission.GROUP_EDIT) is None


def test_the_watch_accepts_a_counted_success_only_under_admin_service_record_change() -> None:
    change = AuditEvent(NOW, "g-1", CHANGES_ALERT.permission.value, Outcome.SUCCESS)
    refusal = AuditEvent(NOW, "g-1", CHANGES_ALERT.permission.value, Outcome.DENIED)
    other = AuditEvent(NOW, "g-1", Permission.GROUP_EDIT.value, Outcome.SUCCESS)
    here = sys._getframe()

    assert checked_by_its_alert(change, recorders.AdminService().record_change())
    assert not checked_by_its_alert(change, here)
    assert checked_by_its_alert(refusal, here)
    assert checked_by_its_alert(other, here)


async def test_the_service_refuses_to_record_a_counted_success_by_hand(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    admin = AdminService(uow, clock, alert_thresholds={Permission.BOOKINGS_VIEW: 20})

    with pytest.raises(ValueError, match=r"bookings\.view"):
        await admin.record("g-1", Permission.BOOKINGS_VIEW.value, Outcome.SUCCESS)
    await admin.record("g-1", Permission.BOOKINGS_VIEW.value, Outcome.DENIED, "no_role")

    assert [(e.action, e.outcome) for e in uow.state.audit] == [("bookings.view", Outcome.DENIED)]
