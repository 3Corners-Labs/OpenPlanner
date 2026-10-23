import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Decision, Permission, Principal, Reason, Role
from app.domain.errors import DomainError
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group

GROUP_ADMIN = Principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())


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
    audit = await service.authorize(ORG_ADMIN, Permission.AUDIT_VIEW, organisation_slug="demo")
    dashboard = await service.authorize(GROUP_ADMIN, Permission.DASHBOARD_VIEW)

    assert audit.decision.allowed
    assert dashboard.decision.allowed
    assert dashboard.group is None


async def test_unknown_group(service: AdminService) -> None:
    with pytest.raises(DomainError, match="group_not_found"):
        await service.authorize(ORG_ADMIN, Permission.GROUP_VIEW, group_slug="nope")


@pytest.mark.parametrize(
    ("permission", "kwargs"),
    [(Permission.GROUP_VIEW, {}), (Permission.AUDIT_VIEW, {})],
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

    assert [e.action for e in await service.audit_log(ORG_ADMIN)] == ["group.edit"]
    assert await service.audit_log(GROUP_ADMIN) == []  # group admins have no audit.view


async def test_authorize_anywhere(service: AdminService, uow: InMemoryUnitOfWork) -> None:
    assert (await service.authorize_anywhere(ORG_ADMIN, Permission.AUDIT_VIEW)).decision.allowed
    assert (
        await service.authorize_anywhere(GROUP_ADMIN, Permission.DASHBOARD_VIEW)
    ).decision.allowed
    denied = await service.authorize_anywhere(GROUP_ADMIN, Permission.AUDIT_VIEW)
    assert denied.decision == Decision(False, Reason.OUT_OF_SCOPE)
    assert [(e.action, e.outcome) for e in uow.state.audit] == [("audit.view", Outcome.DENIED)]
