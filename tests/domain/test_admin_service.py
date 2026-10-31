from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import AuditFilter, Outcome, Source
from app.domain.authorization import Decision, Permission, Principal, Reason, Role
from app.domain.errors import DomainError
from app.domain.staff import Membership, StaffFilter
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.closures import confirmed_booking
from tests.support.domain import NOW, make_group
from tests.support.principals import principal

GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
OTHER_ADMIN = principal(
    "x-1", "X", frozenset({Role.GROUP_ADMIN}), frozenset(), [("andere", "elders")]
)
SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
NO_ROLE = principal("n-1", "N", frozenset(), frozenset({"demo"}), frozenset({"demo"}))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo")
    state.groups[2] = make_group(id=2, organisation_id=2, slug="andere-team")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> AdminService:
    return AdminService(uow, clock)


async def test_allowed_group_permission(service: AdminService, uow: InMemoryUnitOfWork) -> None:
    result = await service.authorize(GROUP_ADMIN, Permission.SCHEDULE_MANAGE, group_slug="demo")

    assert result.decision.allowed
    assert result.group is not None
    assert result.group.organisation_slug == "demo"
    assert uow.state.audit == []


async def test_denial_is_audited(service: AdminService, uow: InMemoryUnitOfWork) -> None:
    result = await service.authorize(GROUP_ADMIN, Permission.GROUP_VIEW, group_slug="andere-team")

    assert not result.decision.allowed
    assert [
        (e.actor, e.action, e.outcome, e.reason, e.organisation, e.group) for e in uow.state.audit
    ] == [("g-1", "group.view", Outcome.DENIED, Reason.OUT_OF_SCOPE.value, "andere", "andere-team")]
    assert uow.state.audit[0].at == NOW


async def test_organisation_and_global_permissions(service: AdminService) -> None:
    branding = await service.authorize(ORG_ADMIN, Permission.GROUP_CREATE, organisation_slug="demo")
    dashboard = await service.authorize(GROUP_ADMIN, Permission.DASHBOARD_VIEW)

    assert branding.decision.allowed
    assert dashboard.decision.allowed
    assert dashboard.group is None


async def test_unknown_group(service: AdminService) -> None:
    with pytest.raises(DomainError, match="group_not_found"):
        await service.authorize(ORG_ADMIN, Permission.GROUP_VIEW, group_slug="nope")


@pytest.mark.parametrize(
    ("permission", "kwargs"),
    [(Permission.GROUP_VIEW, {}), (Permission.GROUP_CREATE, {})],
)
async def test_missing_target_is_a_programming_error(
    service: AdminService, permission: Permission, kwargs: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="needs"):
        await service.authorize(ORG_ADMIN, permission, **kwargs)


async def test_listings_follow_the_matrix(service: AdminService) -> None:
    assert [g.group.slug for g in await service.groups(GROUP_ADMIN)] == ["demo"]
    assert [g.group.slug for g in await service.groups(ORG_ADMIN)] == ["demo"]
    assert await service.groups(GROUP_ADMIN, Permission.GROUP_EDIT) == []


async def test_audit_log_is_scoped(service: AdminService) -> None:
    await service.record("x", "admin.login", Outcome.SUCCESS)
    await service.authorize(GROUP_ADMIN, Permission.GROUP_VIEW, group_slug="andere-team")
    await service.record("o-1", "group.edit", Outcome.SUCCESS, "", "demo", "demo")

    assert [e.event.action for e in await service.audit_log(ORG_ADMIN)] == ["group.edit"]
    assert [e.event.action for e in await service.audit_log(GROUP_ADMIN)] == ["group.edit"]
    assert await service.audit_log(OTHER_ADMIN) == []


async def test_audit_log_shows_no_higher_level(service: AdminService) -> None:
    await service.record("o-1", "holidays.assign", Outcome.SUCCESS, "", "demo")
    await service.record("o-1", "group.edit", Outcome.SUCCESS, "", "demo", "demo")

    assert [e.event.action for e in await service.audit_log(GROUP_ADMIN)] == ["group.edit"]
    assert len(await service.audit_log(ORG_ADMIN)) == 2


async def test_audit_log_filters(service: AdminService) -> None:
    await service.record("o-1", "group.edit", Outcome.SUCCESS, "", "demo", "demo")
    await service.record("o-1", "group.view", Outcome.DENIED, "out_of_scope", "demo", "demo")

    problems = await service.audit_log(ORG_ADMIN, AuditFilter(problems_only=True))

    assert [e.event.action for e in problems] == ["group.view"]
    assert await service.audit_log(ORG_ADMIN, AuditFilter(source=Source.SYSTEM)) == []


async def test_authorize_anywhere(service: AdminService, uow: InMemoryUnitOfWork) -> None:
    assert (await service.authorize_anywhere(ORG_ADMIN, Permission.AUDIT_VIEW)).decision.allowed
    assert (
        await service.authorize_anywhere(GROUP_ADMIN, Permission.DASHBOARD_VIEW)
    ).decision.allowed
    denied = await service.authorize_anywhere(GROUP_ADMIN, Permission.GROUP_CREATE)
    assert denied.decision == Decision(False, Reason.OUT_OF_SCOPE)
    assert [(e.action, e.outcome) for e in uow.state.audit] == [("group.create", Outcome.DENIED)]


def _contexts(uow: InMemoryUnitOfWork) -> list[tuple[str, str, str | None, str | None]]:
    return [(e.actor, e.action, e.organisation, e.group) for e in uow.state.audit]


async def test_login_is_recorded_per_organisation_and_group_in_scope(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    both = principal(
        "b-1",
        "Bea Beide",
        frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
        frozenset({"andere"}),
        frozenset({"demo"}),
    )

    await service.record_login(both)

    assert _contexts(uow) == [
        ("b-1", "admin.login", "andere", None),
        ("b-1", "admin.login", "demo", "demo"),
        ("b-1", "staff.created", "andere", None),
        ("b-1", "staff.created", "demo", "demo"),
    ]
    assert {e.outcome for e in uow.state.audit} == {Outcome.SUCCESS}
    assert {e.reason for e in uow.state.audit[:2]} == {"group_admin,org_admin"}


async def test_auditor_login_is_recorded_in_their_organisation(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    # ADR 0039: the organisation admins and auditors of "demo" can see who checked the log.
    auditor = principal("a-1", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())

    await service.record_login(auditor)

    assert _contexts(uow) == [
        ("a-1", "admin.login", "demo", None),
        ("a-1", "staff.created", "demo", None),
    ]


async def test_claims_without_the_matching_role_give_no_context(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    await service.record_login(SUPER)
    await service.record_logout(NO_ROLE)

    assert _contexts(uow) == [
        ("s-1", "admin.login", None, None),
        ("s-1", "staff.created", None, None),
        ("n-1", "admin.logout", None, None),
    ]


async def test_org_admin_sees_logins_of_admins_in_their_organisation(
    service: AdminService,
) -> None:
    await service.record_login(GROUP_ADMIN)
    await service.record_logout(GROUP_ADMIN)
    await service.record_login(SUPER)

    entries = await service.audit_log(ORG_ADMIN)

    assert [(e.event.action, e.actor_name) for e in entries] == [
        ("admin.logout", "G"),
        ("staff.created", "G"),
        ("admin.login", "G"),
    ]


async def test_audit_log_shows_the_latest_known_name(service: AdminService) -> None:
    await service.record_login(GROUP_ADMIN)
    renamed = Principal("g-1", "Gina Groep", GROUP_ADMIN.grants)
    await service.record_login(renamed)
    await service.record("unknown-subject", "group.edit", Outcome.SUCCESS, "", "demo", "demo")

    names = [e.actor_name for e in await service.audit_log(SUPER)]

    assert names == [None, "Gina Groep", "Gina Groep", "Gina Groep"]


async def test_retention_overview_counts_the_own_organisation_only(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    # ADR 0039: an ended booking still holding personal data two days later is overdue.
    late = confirmed_booking(id=10, start=NOW - timedelta(days=2), end=NOW - timedelta(days=2))
    elsewhere = confirmed_booking(id=11, group_id=2, end=NOW - timedelta(days=2))
    upcoming = confirmed_booking(id=12)
    uow.state.bookings = {b.id or 0: b for b in (late, elsewhere, upcoming)}
    uow.state.failed_purges = {10}

    overview = await service.retention(ORG_ADMIN)
    everything = await service.retention(SUPER)

    rows = [(g.group.group.slug, g.holding, g.overdue, g.failed_purges) for g in overview.groups]
    assert rows == [("demo", 2, 1, 1)]
    assert overview.checked_at == NOW
    assert not overview.in_order
    assert [g.group.group.slug for g in everything.groups] == ["demo", "andere-team"]


async def test_retention_overview_is_empty_without_the_permission(service: AdminService) -> None:
    assert (await service.retention(GROUP_ADMIN)).groups == ()


async def test_a_login_records_the_staff_member_and_what_changed(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    # ADR 0046: just in time from the claims; a move to another group is audited there.
    await service.record_login(GROUP_ADMIN)
    uow.state.audit.clear()
    moved = principal("g-1", "G", GROUP_ADMIN.roles, frozenset(), frozenset({"andere-team"}))

    await service.record_login(moved)

    member = uow.state.staff["g-1"]
    assert member.memberships == {Membership("group_admin", "andere", "andere-team")}
    assert member.first_seen_at == member.last_login_at == member.last_seen_at == NOW
    changes = [(e.organisation, e.group, e.detail) for e in uow.state.audit[1:]]
    assert changes == [
        ("andere", "andere-team", "grant.add:group_admin:andere/andere-team"),
        ("demo", "demo", "grant.remove:group_admin:demo/demo"),
    ]


async def test_seen_and_logout_are_noted(
    service: AdminService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await service.record_login(ORG_ADMIN)
    clock.advance(timedelta(minutes=5))
    await service.record_seen(ORG_ADMIN)
    clock.advance(timedelta(minutes=5))
    await service.record_logout(ORG_ADMIN)

    member = uow.state.staff["o-1"]
    assert member.last_seen_at == NOW + timedelta(minutes=5)
    assert member.last_logout_at == NOW + timedelta(minutes=10)
    assert member.last_login_at == NOW


async def test_staff_are_listed_within_the_viewers_scope(service: AdminService) -> None:
    for admin in (GROUP_ADMIN, ORG_ADMIN, OTHER_ADMIN, SUPER):
        await service.record_login(admin)
    elsewhere = principal(
        "e-1", "E", frozenset({Role.ORG_ADMIN}), frozenset({"demo", "andere"}), frozenset()
    )
    await service.record_login(elsewhere)

    seen_by_org = await service.staff(ORG_ADMIN)
    seen_by_group = await service.staff(GROUP_ADMIN)

    assert [m.subject for m in seen_by_org] == ["e-1", "g-1", "o-1"]
    # Where else a colleague works stays hidden from an organisation admin.
    assert next(m for m in seen_by_org if m.subject == "e-1").organisations == ["demo"]
    assert [m.subject for m in seen_by_group] == ["g-1"]
    assert len(await service.staff(SUPER)) == 5


async def test_a_person_outside_the_scope_is_not_found(service: AdminService) -> None:
    await service.record_login(OTHER_ADMIN)
    await service.record_login(SUPER)
    super_id = (await service.staff(SUPER, StaffFilter(role=Role.SUPERUSER)))[0].id or 0

    assert await service.staff_member(ORG_ADMIN, super_id) is None
    assert await service.staff_member(SUPER, super_id) is not None


# The admin's own organisation (ADR 0042 step 5).


async def test_the_home_organisation_is_the_one_organisation_an_admin_belongs_to() -> None:
    uow = InMemoryUnitOfWork()
    service = AdminService(uow, FixedClock())
    org_admin = principal("o", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
    two = principal("t", "T", frozenset({Role.ORG_ADMIN}), frozenset({"demo", "b"}), frozenset())
    superuser = principal("s", "S", frozenset({Role.SUPERUSER}), frozenset({"demo"}), frozenset())

    assert await service.home_organisation(org_admin) == "demo"
    assert await service.home_organisation(two) is None
    assert await service.home_organisation(superuser) is None
