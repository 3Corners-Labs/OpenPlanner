"""MaintenanceService (ADR 0038): switching, the environment override, the cache, refusals.

Abuse cases from docs/threat-model.md: an organisation admin switching maintenance on or off,
and switching off what the environment keeps on.
"""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Target
from app.domain.errors import DomainError, ErrorCode
from app.domain.maintenance import OFF, MaintenanceReason, MaintenanceState
from app.domain.maintenance_service import MaintenanceService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW

CACHE = timedelta(seconds=5)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


def service(
    uow: InMemoryUnitOfWork, clock: FixedClock, *, environment: bool = False
) -> MaintenanceService:
    return MaintenanceService(
        uow, clock, AdminService(uow, clock), environment=environment, cache=CACHE
    )


async def test_superuser_switches_on_and_off(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    maintenance = service(uow, clock)
    end = NOW + timedelta(hours=1)

    on = await maintenance.switch_on(DEMO_SUPERUSER, MaintenanceReason.INTEGRATION, end)
    assert on == MaintenanceState(True, NOW, "s-1", end, MaintenanceReason.INTEGRATION)
    assert (await maintenance.status()).active

    await maintenance.switch_off(DEMO_SUPERUSER)
    assert not (await maintenance.status()).active

    details = [(e.action, e.detail, e.reason) for e in uow.state.audit]
    assert details == [
        ("system.maintenance", "maintenance.on", "integration"),
        ("system.maintenance", "maintenance.off", ""),
    ]


async def test_switching_off_when_off_records_nothing(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    assert await service(uow, clock).switch_off(DEMO_SUPERUSER) == OFF
    assert uow.state.audit == []


async def test_organisation_admin_cannot_switch(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    maintenance = service(uow, clock)

    for attempt in (
        maintenance.switch_on(DEMO_ORG_ADMIN, MaintenanceReason.OTHER, None),
        maintenance.switch_off(DEMO_ORG_ADMIN),
        maintenance.overview(DEMO_ORG_ADMIN),
    ):
        with pytest.raises(DomainError) as raised:
            await attempt
        assert raised.value.code is ErrorCode.NOT_PERMITTED

    assert uow.state.maintenance == OFF
    assert {e.outcome for e in uow.state.audit} == {Outcome.DENIED}


async def test_environment_keeps_it_on_without_reading_the_database(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    maintenance = service(uow, clock, environment=True)

    status = await maintenance.status()

    assert status.active
    assert status.environment
    assert uow.reads == 0


async def test_environment_override_cannot_be_switched_off_in_the_portal(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    maintenance = service(uow, clock, environment=True)
    await maintenance.switch_on(DEMO_SUPERUSER, MaintenanceReason.OTHER, None)

    with pytest.raises(DomainError) as raised:
        await maintenance.switch_off(DEMO_SUPERUSER)

    assert raised.value.code is ErrorCode.MAINTENANCE_FROM_ENVIRONMENT
    assert uow.state.maintenance.on
    overview = await maintenance.overview(DEMO_SUPERUSER)
    assert overview.environment
    assert overview.state.on


async def test_status_is_cached_for_at_most_the_cache_period(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    web = service(uow, clock)
    cli = service(uow, clock)
    assert not (await web.status()).active

    await cli.switch_on_host("host-updater", MaintenanceReason.UPDATE)
    clock.advance(CACHE - timedelta(milliseconds=1))
    assert not (await web.status()).active

    clock.advance(timedelta(milliseconds=1))
    assert (await web.status()).active


async def test_a_switch_in_this_process_is_seen_at_once(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    maintenance = service(uow, clock)
    assert not (await maintenance.status()).active

    await maintenance.switch_on(DEMO_SUPERUSER, MaintenanceReason.OTHER, None)
    assert (await maintenance.status()).active

    await maintenance.switch_off(DEMO_SUPERUSER)
    assert not (await maintenance.status()).active


async def test_host_switches_are_audited_as_the_actor(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    maintenance = service(uow, clock)

    await maintenance.switch_on_host("host-updater", MaintenanceReason.UPDATE)
    assert (await maintenance.stored()).state.by == "host-updater"
    await maintenance.switch_off_host("host-updater")

    assert [(e.actor, e.detail) for e in uow.state.audit] == [
        ("host-updater", "maintenance.on"),
        ("host-updater", "maintenance.off"),
    ]
    assert all(e.organisation is None and e.group is None for e in uow.state.audit)


async def test_refusal_is_audited_with_the_permission_only(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    target = Target.for_group("demo", "demo")

    await service(uow, clock).refuse(DEMO_ORG_ADMIN, Permission.SCHEDULE_MANAGE, target)

    [event] = uow.state.audit
    assert (event.action, event.outcome, event.reason, event.detail) == (
        "schedule.manage",
        Outcome.DENIED,
        "maintenance",
        "maintenance.refused",
    )
    assert (event.organisation, event.group) == ("demo", "demo")
