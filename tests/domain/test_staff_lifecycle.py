"""Suspending, reinstating and erasing staff records (ADR 0046 step 2)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin_sessions import AdminSession, EndReason
from app.domain.audit import SYSTEM_ACTOR, Outcome, Source
from app.domain.authorization import Role
from app.domain.entitlements import LoginClaims
from app.domain.staff import (
    ERASED_BY_RETENTION,
    ERASED_EVENT,
    ERASED_MANUALLY,
    REINSTATED_EVENT,
    SUSPENDED_EVENT,
    Membership,
    RefusedEntitlement,
    StaffMember,
    StaffStatus,
)
from app.domain.staff_lifecycle import Result, StaffLifecycleService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

DEMO = Membership("org_admin", "demo")
RETENTION = timedelta(days=365)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> StaffLifecycleService:
    return StaffLifecycleService(uow, clock)


def _person(
    uow: InMemoryUnitOfWork,
    subject: str = "p-1",
    *,
    roles: frozenset[Role] = frozenset({Role.GROUP_ADMIN}),
    memberships: frozenset[Membership] = frozenset({DEMO}),
    last_login_days_ago: int = 1,
    **changes: object,
) -> StaffMember:
    login = NOW - timedelta(days=last_login_days_ago)
    member = StaffMember(
        subject,
        "Paula Person",
        roles,
        memberships,
        login,
        login,
        login,
        id=len(uow.state.staff) + 10,
        refused=(RefusedEntitlement("op:x", "unknown", "demo", login),),
    )
    member = replace(member, **changes)  # type: ignore[arg-type]
    uow.state.staff[subject] = member
    return member


def _session(uow: InMemoryUnitOfWork, subject: str, id_hash: str) -> None:
    uow.state.admin_sessions.append(
        AdminSession(id_hash, subject, "platform", LoginClaims(subject, "N"), NOW, NOW)
    )


def _audit(uow: InMemoryUnitOfWork) -> list[tuple[str, str, Outcome, str, str | None, str]]:
    return [
        (e.actor, e.action, e.outcome, e.reason, e.organisation, e.detail) for e in uow.state.audit
    ]


async def test_suspending_blocks_the_person_and_ends_their_sessions(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService, clock: FixedClock
) -> None:
    person = _person(uow)
    _session(uow, "p-1", "h1")
    _session(uow, "p-1", "h2")
    _session(uow, "other", "h3")

    result = await service.suspend(ORG_ADMIN, person.id or 0)

    assert result.outcome is Result.DONE
    assert result.sessions == 2
    assert uow.state.staff["p-1"].status is StaffStatus.SUSPENDED
    assert uow.state.staff["p-1"].status_at == clock.now()
    ended = {s.id_hash: (s.ended_at, s.end_reason) for s in uow.state.admin_sessions}
    assert ended["h1"] == (clock.now(), EndReason.SUSPENDED)
    assert ended["h2"] == (clock.now(), EndReason.SUSPENDED)
    assert ended["h3"] == (None, None)  # somebody else's session is untouched
    assert _audit(uow) == [
        ("o-1", SUSPENDED_EVENT, Outcome.SUCCESS, "", "demo", "sessions:2,subject:p-1")
    ]


async def test_suspending_twice_changes_nothing_and_writes_no_second_event(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    person = _person(uow)
    await service.suspend(ORG_ADMIN, person.id or 0)

    again = await service.suspend(ORG_ADMIN, person.id or 0)

    assert again.outcome is Result.UNCHANGED
    assert len(uow.state.audit) == 1


async def test_reinstating_lifts_the_suspension_and_is_audited(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    person = _person(uow, status=StaffStatus.SUSPENDED)

    result = await service.reinstate(ORG_ADMIN, person.id or 0)

    assert result.outcome is Result.DONE
    assert uow.state.staff["p-1"].status is StaffStatus.ACTIVE
    assert [row[1] for row in _audit(uow)] == [REINSTATED_EVENT]
    again = await service.reinstate(ORG_ADMIN, person.id or 0)
    assert again.outcome is Result.UNCHANGED


async def test_nobody_suspends_themselves(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    me = _person(uow, "o-1", roles=frozenset({Role.ORG_ADMIN}))

    result = await service.suspend(ORG_ADMIN, me.id or 0)

    assert result.outcome is Result.SELF
    assert uow.state.staff["o-1"].status is StaffStatus.ACTIVE
    assert _audit(uow)[0][2:5] == (Outcome.DENIED, "self", None)


async def test_an_organisation_admin_cannot_suspend_a_superuser(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    boss = _person(uow, "boss", roles=frozenset({Role.SUPERUSER}))

    result = await service.suspend(ORG_ADMIN, boss.id or 0)

    # A superuser is not even in the organisation admin's overview: it does not exist for them.
    assert result.outcome is Result.OUT_OF_SCOPE
    assert uow.state.staff["boss"].status is StaffStatus.ACTIVE
    assert _audit(uow)[0][1:4] == ("staff.manage", Outcome.DENIED, "out_of_scope")


async def test_the_superuser_may_suspend_a_superuser_but_not_themselves(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    other = _person(uow, "boss", roles=frozenset({Role.SUPERUSER}))
    me = _person(uow, "s-1", roles=frozenset({Role.SUPERUSER}))

    assert (await service.suspend(SUPER, other.id or 0)).outcome is Result.DONE
    assert (await service.suspend(SUPER, me.id or 0)).outcome is Result.SELF


async def test_a_person_outside_the_scope_is_refused_and_audited(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    person = _person(uow)

    result = await service.suspend(OUTSIDER, person.id or 0)
    unknown = await service.suspend(OUTSIDER, 999)

    assert result.outcome is Result.OUT_OF_SCOPE
    assert unknown.outcome is Result.OUT_OF_SCOPE
    assert uow.state.staff["p-1"].status is StaffStatus.ACTIVE
    assert [row[2:4] for row in _audit(uow)] == [(Outcome.DENIED, "out_of_scope")] * 2


async def test_an_erased_record_cannot_be_suspended_or_reinstated(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    person = _person(uow)
    uow.state.staff["p-1"] = person.erased(NOW)

    assert (await service.suspend(SUPER, person.id or 0)).outcome is Result.ERASED
    assert (await service.reinstate(SUPER, person.id or 0)).outcome is Result.ERASED


async def test_erasing_removes_name_roles_and_memberships_but_keeps_the_subject(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService, clock: FixedClock
) -> None:
    person = _person(uow, status=StaffStatus.SUSPENDED)
    _session(uow, "p-1", "h1")

    result = await service.erase(SUPER, person.id or 0)

    assert result.outcome is Result.DONE
    erased = uow.state.staff["p-1"]
    assert (erased.display_name, erased.roles, erased.memberships, erased.refused) == (
        "",
        frozenset(),
        frozenset(),
        (),
    )
    assert (erased.subject, erased.id, erased.status, erased.status_at) == (
        "p-1",
        person.id,
        StaffStatus.ERASED,
        clock.now(),
    )
    assert uow.state.admin_sessions[0].end_reason is EndReason.ERASED
    assert _audit(uow) == [
        ("s-1", ERASED_EVENT, Outcome.SUCCESS, ERASED_MANUALLY, "demo", "sessions:1,subject:p-1")
    ]
    assert (await service.erase(SUPER, person.id or 0)).outcome is Result.UNCHANGED


async def test_erasing_needs_a_suspension_first_and_never_the_own_record(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    active = _person(uow)
    me = _person(uow, "s-1", roles=frozenset({Role.SUPERUSER}), status=StaffStatus.SUSPENDED)

    assert (await service.erase(SUPER, active.id or 0)).outcome is Result.NOT_SUSPENDED
    assert (await service.erase(SUPER, me.id or 0)).outcome is Result.SELF
    assert uow.state.staff["p-1"].display_name == "Paula Person"
    assert (await service.erase(SUPER, 999)).outcome is Result.OUT_OF_SCOPE


async def test_retention_erases_only_those_without_a_login_for_the_period(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    _person(uow, "old", last_login_days_ago=366)
    _person(uow, "edge", last_login_days_ago=365)
    _person(uow, "recent", last_login_days_ago=364)
    _person(uow, "gone", last_login_days_ago=500, status=StaffStatus.SUSPENDED)
    _session(uow, "old", "h-old")

    erased = await service.erase_inactive(RETENTION)

    assert erased == 2  # old and gone; "edge" has a login exactly at the cut-off
    states = {s: m.status for s, m in uow.state.staff.items()}
    assert states == {
        "old": StaffStatus.ERASED,
        "edge": StaffStatus.ACTIVE,
        "recent": StaffStatus.ACTIVE,
        "gone": StaffStatus.ERASED,
    }
    assert uow.state.staff["old"].display_name == ""
    assert uow.state.staff["recent"].display_name == "Paula Person"
    assert uow.state.admin_sessions[0].end_reason is EndReason.ERASED
    events = [(e.actor, e.action, e.reason, e.source) for e in uow.state.audit]
    assert events == [(SYSTEM_ACTOR, ERASED_EVENT, ERASED_BY_RETENTION, Source.SYSTEM)] * 2


async def test_the_retention_run_is_idempotent(
    uow: InMemoryUnitOfWork, service: StaffLifecycleService
) -> None:
    _person(uow, "old", last_login_days_ago=400)

    assert await service.erase_inactive(RETENTION) == 1
    events = len(uow.state.audit)

    assert await service.erase_inactive(RETENTION) == 0
    assert len(uow.state.audit) == events
