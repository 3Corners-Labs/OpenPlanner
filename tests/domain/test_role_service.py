"""RoleService: the superuser's role editor, change history and claim linking (ADR 0039).

Abuse cases from docs/threat-model.md: a non-superuser changes roles, a definition smuggles in
a locked permission or one above its reach, a claim value collides with a built-in role's
(the superuser's above all) or another custom role's, and a stale form overwrites a change.
"""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome, Source
from app.domain.authorization import (
    EVERY_ADMIN,
    MATRIX,
    CustomRole,
    Grant,
    Permission,
    Principal,
    Role,
    RoleRule,
    Target,
    Violation,
    decide,
    delegable,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.roles import (
    MAX_DETAIL_LENGTH,
    ROLE_CREATED,
    ROLE_DELETED,
    ROLE_UPDATED,
    RoleDraft,
    RoleInvalidError,
    RoleService,
    change_detail,
    is_claim_value,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW
from tests.support.principals import principal

NOBODY: frozenset[str] = frozenset()
SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), NOBODY, NOBODY)
ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), NOBODY)
RESERVED = ("openplanner-superuser", "openplanner-org-admin")


def draft(**overrides: object) -> RoleDraft:
    fields: dict[str, object] = {
        "template": "group_admin",
        "permissions": [p.value for p in MATRIX[Role.GROUP_ADMIN] | {Permission.GROUP_EDIT}],
        "claim_value": "openplanner-teamlead",
        "name_nl": "Teamleider",
        "name_en": "Team lead",
    }
    fields.update(overrides)
    return RoleDraft.create(**fields)  # type: ignore[arg-type]


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> RoleService:
    clock = FixedClock(NOW)
    return RoleService(uow, clock, AdminService(uow, clock), RESERVED)


# Drafts.


def test_a_draft_normalises_names_and_claim_value() -> None:
    made = draft(name_nl="  Team   leider ", claim_value=" openplanner-teamlead ")
    assert (made.name_nl, made.claim_value) == ("Team leider", "openplanner-teamlead")
    assert made.template is Role.GROUP_ADMIN


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name_nl": " "}, "name_nl"),
        ({"name_en": "x" * 81}, "name_en"),
        ({"name_en": "Team\x00lead"}, "name_en"),
        ({"claim_value": "two words"}, "claim_value"),
        ({"claim_value": "a,b"}, "claim_value"),
        ({"claim_value": ""}, "claim_value"),
        ({"claim_value": "x" * 129}, "claim_value"),
        ({"template": "nope"}, "permissions"),
        ({"permissions": ["nope.view"]}, "permissions"),
    ],
)
def test_a_draft_refuses_bad_input_naming_the_field(
    overrides: dict[str, object], field: str
) -> None:
    with pytest.raises(DomainError) as refused:
        draft(**overrides)
    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == field


def test_a_draft_smuggling_a_locked_or_out_of_reach_permission_is_refused() -> None:
    with pytest.raises(RoleInvalidError) as refused:
        draft(
            permissions=[
                *(p.value for p in MATRIX[Role.GROUP_ADMIN]),
                "integrations.manage",
                "group.create",
            ]
        )
    assert refused.value.code is ErrorCode.ROLE_INVALID
    assert refused.value.violations == [
        Violation(RoleRule.LOCKED, Permission.INTEGRATIONS_MANAGE),
        Violation(RoleRule.ABOVE_REACH, Permission.GROUP_CREATE),
    ]


def test_an_auditor_copy_with_personal_data_is_refused() -> None:
    with pytest.raises(RoleInvalidError) as refused:
        draft(
            template="auditor",
            permissions=[*(p.value for p in MATRIX[Role.AUDITOR]), "bookings.view"],
        )
    assert refused.value.violations == [Violation(RoleRule.PII, Permission.BOOKINGS_VIEW)]


def test_claim_values_that_can_be_kept_in_the_session() -> None:
    assert is_claim_value("CN=openplanner-lead/OU=kcc@gemeente.nl")
    assert not is_claim_value("<script>")


# The editor.


async def test_the_superuser_creates_a_role_at_version_1_with_an_audit_event(
    service: RoleService, uow: InMemoryUnitOfWork
) -> None:
    role = await service.create(SUPER, draft())

    assert (role.version, role.key) == (1, f"custom.{role.id}")
    [event] = uow.state.audit
    assert (event.action, event.outcome, event.source) == (
        ROLE_CREATED,
        Outcome.SUCCESS,
        Source.ADMIN,
    )
    assert (event.organisation, event.group) == (None, None)  # global level
    assert event.reason == role.key
    assert event.detail == "version:1,template:group_admin,perm.add:group.edit"
    assert "Teamleider" not in event.detail


async def test_a_non_superuser_cannot_touch_roles_and_is_audited(
    service: RoleService, uow: InMemoryUnitOfWork
) -> None:
    role = await service.create(SUPER, draft())
    for attempt in (
        service.create(ORG_ADMIN, draft(claim_value="other")),
        service.update(ORG_ADMIN, role.id, draft(), role.version),
        service.delete(ORG_ADMIN, role.id, role.version),
        service.role(ORG_ADMIN, role.id),
    ):
        with pytest.raises(DomainError) as refused:
            await attempt
        assert refused.value.code is ErrorCode.NOT_PERMITTED
    denials = [e for e in uow.state.audit if e.outcome is Outcome.DENIED]
    assert [e.action for e in denials] == ["roles.manage"] * 4
    assert (await service.catalogue()).custom == (role,)


@pytest.mark.parametrize(
    "value",
    [
        "openplanner-superuser",
        "OPENPLANNER-SUPERUSER",
        "openplanner-org-admin",
        "openplanner-teamlead",
    ],
)
async def test_a_claim_value_may_not_collide(service: RoleService, value: str) -> None:
    await service.create(SUPER, draft())
    with pytest.raises(DomainError) as refused:
        await service.create(SUPER, draft(claim_value=value))
    assert (refused.value.code, refused.value.field) == (ErrorCode.CLAIM_VALUE_TAKEN, "claim_value")


async def test_an_update_bumps_the_version_and_records_what_changed(
    service: RoleService, uow: InMemoryUnitOfWork
) -> None:
    role = await service.create(SUPER, draft())
    permissions = [p.value for p in MATRIX[Role.GROUP_ADMIN] - {Permission.TEXTS_MANAGE}]

    changed = await service.update(
        SUPER, role.id, draft(permissions=permissions, name_en="Lead"), role.version
    )

    assert changed
    stored = await service.role(SUPER, role.id)
    assert stored.version == 2
    assert Permission.GROUP_EDIT not in stored.permissions
    assert uow.state.audit[-1].action == ROLE_UPDATED
    assert uow.state.audit[-1].reason == role.key
    assert uow.state.audit[-1].detail == (
        "version:2,perm.remove:group.edit,perm.remove:texts.manage,name"
    )


async def test_an_update_keeping_its_own_claim_value_is_fine_and_nothing_is_unchanged(
    service: RoleService, uow: InMemoryUnitOfWork
) -> None:
    role = await service.create(SUPER, draft())
    assert not await service.update(SUPER, role.id, draft(), role.version)
    assert (await service.role(SUPER, role.id)).version == 1
    assert len(uow.state.audit) == 1


async def test_a_stale_form_is_refused(service: RoleService) -> None:
    role = await service.create(SUPER, draft())
    await service.update(SUPER, role.id, draft(name_nl="Leider"), role.version)
    with pytest.raises(DomainError) as refused:
        await service.update(SUPER, role.id, draft(name_nl="Anders"), role.version)
    assert refused.value.code is ErrorCode.ROLE_CHANGED


async def test_the_template_never_changes(service: RoleService) -> None:
    role = await service.create(SUPER, draft())
    auditor = draft(template="auditor", permissions=[p.value for p in MATRIX[Role.AUDITOR]])
    with pytest.raises(DomainError) as refused:
        await service.update(SUPER, role.id, auditor, role.version)
    assert refused.value.field == "template"


async def test_unknown_roles(service: RoleService) -> None:
    for attempt in (service.role(SUPER, 99), service.delete(SUPER, 99, 1)):
        with pytest.raises(DomainError) as refused:
            await attempt
        assert refused.value.code is ErrorCode.ROLE_NOT_FOUND


async def test_a_change_takes_effect_on_the_next_request_and_delete_leaves_no_role(
    service: RoleService, uow: InMemoryUnitOfWork
) -> None:
    role = await service.create(SUPER, draft())
    session = Principal("t-1", "T", frozenset({Grant(role.key, "demo", "demo")}))
    own_group = Target.for_group("demo", "demo")

    assert decide(await service.attach(session), Permission.GROUP_EDIT, own_group).allowed

    edited = draft(permissions=[p.value for p in EVERY_ADMIN | {Permission.GROUP_VIEW}])
    await service.update(SUPER, role.id, edited, role.version)
    assert not decide(await service.attach(session), Permission.GROUP_EDIT, own_group).allowed

    await service.delete(SUPER, role.id, 2)
    after = await service.attach(session)
    assert decide(after, Permission.DASHBOARD_VIEW, Target.everything()).reason.value == "no_role"
    assert uow.state.audit[-1].action == ROLE_DELETED
    assert (uow.state.audit[-1].reason, uow.state.audit[-1].detail) == (role.key, "version:2")


async def test_attach_reads_nothing_without_unmatched_claim_values(service: RoleService) -> None:
    assert await service.attach(ORG_ADMIN) is ORG_ADMIN


async def test_history_lists_role_changes_newest_first(service: RoleService) -> None:
    role = await service.create(SUPER, draft())
    await service.update(SUPER, role.id, draft(name_nl="Leider"), 1)
    with pytest.raises(DomainError):
        await service.create(ORG_ADMIN, draft(claim_value="x"))

    history = await service.history()

    assert [entry.event.action for entry in history] == [ROLE_UPDATED, ROLE_CREATED]


@pytest.mark.parametrize("template", [Role.ORG_ADMIN, Role.GROUP_ADMIN, Role.AUDITOR])
def test_a_detail_always_fits_and_is_never_cut_off(template: Role) -> None:
    # Worst case: every delegable permission flips, plus both other fields.
    offered = delegable(template)
    before = CustomRole(9, template, offered - MATRIX[template], "a", "A", "A", 99)
    after = CustomRole(9, template, MATRIX[template], "b", "B", "B", 100)
    for detail in (change_detail(after, before), change_detail(before, None)):
        assert len(detail) <= MAX_DETAIL_LENGTH
        assert detail.startswith("version:")
    assert change_detail(after, before).endswith("name,claim_value")
