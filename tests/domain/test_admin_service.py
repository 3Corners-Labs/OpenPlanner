import pytest

from app.domain.admin import AdminService
from app.domain.audit import AuditFilter, Outcome, Source
from app.domain.authorization import Decision, Permission, Principal, Reason, Role
from app.domain.errors import DomainError
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group

GROUP_ADMIN = Principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
OTHER_ADMIN = Principal(
    "x-1", "X", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"elders"})
)
SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
NO_ROLE = Principal("n-1", "N", frozenset(), frozenset({"demo"}), frozenset({"demo"}))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo")
    state.groups[2] = make_group(id=2, organisation_id=2, slug="andere-team")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> AdminService:
    return AdminService(uow, FixedClock(NOW))


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
    both = Principal(
        "b-1",
        "Bea Beide",
        frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
        frozenset({"andere"}),
        frozenset({"demo", "unknown-group"}),
    )

    await service.record_login(both)

    assert _contexts(uow) == [
        ("b-1", "admin.login", "andere", None),
        ("b-1", "admin.login", "demo", "demo"),
    ]
    assert {e.outcome for e in uow.state.audit} == {Outcome.SUCCESS}
    assert {e.reason for e in uow.state.audit} == {"group_admin,org_admin"}


async def test_auditor_login_is_recorded_in_their_organisation(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    # ADR 0037: the organisation admins and auditors of "demo" can see who checked the log.
    auditor = Principal("a-1", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())

    await service.record_login(auditor)

    assert _contexts(uow) == [("a-1", "admin.login", "demo", None)]


async def test_claims_without_the_matching_role_give_no_context(
    service: AdminService, uow: InMemoryUnitOfWork
) -> None:
    await service.record_login(SUPER)
    await service.record_logout(NO_ROLE)

    assert _contexts(uow) == [
        ("s-1", "admin.login", None, None),
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
        ("admin.login", "G"),
    ]


async def test_audit_log_shows_the_latest_known_name(service: AdminService) -> None:
    await service.record_login(GROUP_ADMIN)
    renamed = Principal(
        "g-1", "Gina Groep", GROUP_ADMIN.roles, GROUP_ADMIN.organisations, GROUP_ADMIN.groups
    )
    await service.record_login(renamed)
    await service.record("unknown-subject", "group.edit", Outcome.SUCCESS, "", "demo", "demo")

    names = [e.actor_name for e in await service.audit_log(SUPER)]

    assert names == [None, "Gina Groep", "Gina Groep"]
