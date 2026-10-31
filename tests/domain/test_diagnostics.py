"""Diagnostic mode (ADR 0040, part 4): superuser only, audited, ends by itself, brake."""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Category, Level, Source
from app.domain.authorization import Role
from app.domain.diagnostics import BRAKE_EVENT, DiagnosticLog, DiagnosticsService
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group
from tests.support.principals import principal

SUPER = principal("super-1", "Sam", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = principal(
    "org-1", "Olga", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset()
)
HOURS = timedelta(hours=24)


def _setup() -> tuple[DiagnosticsService, DiagnosticLog, InMemoryUnitOfWork, FixedClock]:
    state = State()
    state.organisations[1] = Organisation(1, "Demo", "demo")
    state.organisation_slugs[1] = "demo"
    state.groups[1] = make_group(id=1, organisation_id=1, slug="aangifte")
    state.groups[2] = make_group(id=2, organisation_id=1, slug="paspoort")
    uow, clock = InMemoryUnitOfWork(state), FixedClock(NOW)
    service = DiagnosticsService(uow, clock, AdminService(uow, clock), HOURS)
    return service, DiagnosticLog(uow, clock), uow, clock


async def test_debug_events_are_written_only_for_a_group_in_diagnostic_mode() -> None:
    service, debug, uow, _ = _setup()

    assert not await debug.debug("job.run", "demo", "aangifte", detail="type:x")
    await service.switch_on(SUPER, "demo", "aangifte")
    assert await debug.debug("job.run", "demo", "aangifte", booking_id=7, detail="type:x")
    assert not await debug.debug("job.run", "demo", "paspoort")

    (event,) = [e for e in uow.state.audit if e.category is Category.DIAGNOSTIC]
    assert (event.level, event.source, event.group, event.booking_id) == (
        Level.DEBUG,
        Source.SYSTEM,
        "aangifte",
        7,
    )


async def test_an_organisation_mode_covers_all_its_groups() -> None:
    service, debug, _, _ = _setup()

    await service.switch_on(SUPER, "demo")

    assert await debug.debug("job.run", "demo", "paspoort")
    assert await debug.debug("job.run", "demo", None)
    assert not await debug.debug("job.run", "andere", "x")
    assert not await debug.debug("job.run", None, None)


async def test_the_mode_ends_by_itself() -> None:
    service, debug, _, clock = _setup()
    await service.switch_on(SUPER, "demo", "aangifte")

    clock.advance(HOURS)

    assert await service.active() == []
    assert not await debug.debug("job.run", "demo", "aangifte")


async def test_switching_is_audited_and_off_ends_it() -> None:
    service, debug, uow, _ = _setup()

    mode = await service.switch_on(SUPER, "demo", "aangifte")
    await service.switch_off(SUPER, "demo", "aangifte")
    await service.switch_off(SUPER, "demo", "aangifte")  # already off: nothing to audit

    assert mode.until == NOW + HOURS
    assert [(e.action, e.detail, e.actor) for e in uow.state.audit] == [
        ("diagnostics.manage", "diagnostics.on", "super-1"),
        ("diagnostics.manage", "diagnostics.off", "super-1"),
    ]
    assert not await debug.debug("job.run", "demo", "aangifte")


async def test_only_the_superuser_may_switch() -> None:
    # Product owner, 2026-10-29: not the organisation or group admin.
    service, _, uow, _ = _setup()

    with pytest.raises(DomainError) as refused:
        await service.switch_on(ORG_ADMIN, "demo", "aangifte")

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.diagnostic_modes == []


@pytest.mark.parametrize(
    ("organisation", "group", "code"),
    [
        ("demo", "onbekend", ErrorCode.GROUP_NOT_FOUND),
        ("andere", "aangifte", ErrorCode.GROUP_NOT_FOUND),
        ("onbekend", None, ErrorCode.ORGANISATION_NOT_FOUND),
    ],
)
async def test_an_unknown_target_is_refused(
    organisation: str, group: str | None, code: ErrorCode
) -> None:
    service, _, _, _ = _setup()

    with pytest.raises(DomainError) as refused:
        await service.switch_on(SUPER, organisation, group)

    assert refused.value.code is code


async def test_the_brake_ends_every_mode_and_says_so() -> None:
    service, debug, uow, _ = _setup()
    await service.switch_on(SUPER, "demo", "aangifte")
    await service.switch_on(SUPER, "demo")

    assert await service.brake("free_space_critical") == 2
    assert await service.brake("free_space_critical") == 0  # nothing left: no second event

    brakes = [e for e in uow.state.audit if e.action == BRAKE_EVENT]
    assert [(e.reason, e.detail, e.organisation) for e in brakes] == [
        ("free_space_critical", "modes:2", None)
    ]
    assert not await debug.debug("job.run", "demo", "aangifte")
