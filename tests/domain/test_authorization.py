"""The authorisation matrix (ADR 0014): decide() and scope_for() must always agree."""

import itertools

import pytest

from app.domain.authorization import (
    LEVELS,
    MATRIX,
    Decision,
    Level,
    Permission,
    Principal,
    Reason,
    Role,
    Scope,
    Target,
    decide,
    permissions_for,
    scope_for,
)

SUPER = Principal("s", "Super", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG = Principal("o", "Org", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP = Principal("g", "Group", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
NOBODY = Principal("n", "Nobody", frozenset(), frozenset({"demo"}), frozenset({"demo"}))
MIXED = Principal(
    "m",
    "Mixed",
    frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
    frozenset({"andere"}),
    frozenset({"demo"}),
)

OWN_GROUP = Target.for_group("demo", "demo")
OTHER_GROUP = Target.for_group("toeslagen", "andere")
OWN_ORG = Target.for_organisation("demo")
OTHER_ORG = Target.for_organisation("andere")


def test_matrix_follows_handover_section_6() -> None:
    assert MATRIX[Role.SUPERUSER] == frozenset(Permission)
    assert MATRIX[Role.ORG_ADMIN] == {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.AUDIT_VIEW,
        Permission.HOLIDAYS_ASSIGN,  # ADR 0022
        Permission.GROUP_CREATE,
        Permission.GROUP_VIEW,
        Permission.GROUP_EDIT,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.BOOKINGS_VIEW,
    }
    assert MATRIX[Role.GROUP_ADMIN] == {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.GROUP_VIEW,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.BOOKINGS_VIEW,
    }


def test_every_permission_has_a_level() -> None:
    assert set(LEVELS) == set(Permission)


@pytest.mark.parametrize(
    ("principal", "permission", "target", "expected"),
    [
        (SUPER, Permission.INTEGRATIONS_MANAGE, OTHER_ORG, Decision(True, Reason.SUPERUSER)),
        (SUPER, Permission.GROUP_EDIT, OTHER_GROUP, Decision(True, Reason.SUPERUSER)),
        (ORG, Permission.GROUP_EDIT, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (ORG, Permission.GROUP_EDIT, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (ORG, Permission.INTEGRATIONS_MANAGE, OWN_ORG, Decision(False, Reason.NOT_PERMITTED)),
        (ORG, Permission.AUDIT_VIEW, OWN_ORG, Decision(True, Reason.ORGANISATION)),
        (GROUP, Permission.SCHEDULE_MANAGE, OWN_GROUP, Decision(True, Reason.GROUP)),
        (GROUP, Permission.SCHEDULE_MANAGE, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (GROUP, Permission.GROUP_EDIT, OWN_GROUP, Decision(False, Reason.NOT_PERMITTED)),
        (GROUP, Permission.AUDIT_VIEW, OWN_ORG, Decision(False, Reason.NOT_PERMITTED)),
        (GROUP, Permission.DASHBOARD_VIEW, Target.everything(), Decision(True, Reason.ROLE)),
        (NOBODY, Permission.DASHBOARD_VIEW, Target.everything(), Decision(False, Reason.NO_ROLE)),
        (NOBODY, Permission.GROUP_VIEW, OWN_GROUP, Decision(False, Reason.NO_ROLE)),
        (
            SUPER,
            Permission.ORGANISATION_MANAGE,
            Target.everything(),
            Decision(True, Reason.SUPERUSER),
        ),
        (
            ORG,
            Permission.ORGANISATION_MANAGE,
            Target.everything(),
            Decision(False, Reason.NOT_PERMITTED),
        ),
        # Roles combine: org admin elsewhere, group admin here.
        (MIXED, Permission.SCHEDULE_MANAGE, OWN_GROUP, Decision(True, Reason.GROUP)),
        (MIXED, Permission.GROUP_EDIT, OWN_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (MIXED, Permission.GROUP_EDIT, OTHER_GROUP, Decision(True, Reason.ORGANISATION)),
    ],
)
def test_decide(
    principal: Principal, permission: Permission, target: Target, expected: Decision
) -> None:
    assert decide(principal, permission, target) == expected


def test_target_level_must_match_the_permission() -> None:
    with pytest.raises(ValueError, match="level"):
        decide(SUPER, Permission.GROUP_VIEW, OWN_ORG)


def test_scope_for_group_permissions() -> None:
    assert scope_for(SUPER, Permission.GROUP_VIEW) == Scope(everything=True)
    assert scope_for(ORG, Permission.GROUP_VIEW) == Scope(organisations=frozenset({"demo"}))
    assert scope_for(GROUP, Permission.GROUP_VIEW) == Scope(groups=frozenset({"demo"}))
    assert scope_for(GROUP, Permission.GROUP_EDIT).is_empty
    assert scope_for(NOBODY, Permission.GROUP_VIEW).is_empty


def test_scope_for_rejects_global_permissions() -> None:
    with pytest.raises(ValueError, match="global"):
        scope_for(SUPER, Permission.DASHBOARD_VIEW)


def test_scope_contains() -> None:
    scope = Scope(organisations=frozenset({"demo"}), groups=frozenset({"x"}))

    assert scope.contains("demo", "anything")
    assert scope.contains("andere", "x")
    assert not scope.contains("andere", "y")
    assert Scope(everything=True).contains("a", "b")
    assert not Scope().contains("a", "b")


PRINCIPALS = [SUPER, ORG, GROUP, NOBODY, MIXED]
GROUP_TARGETS = [OWN_GROUP, OTHER_GROUP, Target.for_group("losse", "demo")]
GROUP_PERMISSIONS = [p for p in Permission if LEVELS[p] is Level.GROUP]


@pytest.mark.parametrize(
    ("principal", "permission", "target"),
    list(itertools.product(PRINCIPALS, GROUP_PERMISSIONS, GROUP_TARGETS)),
)
def test_database_scope_and_decision_never_disagree(
    principal: Principal, permission: Permission, target: Target
) -> None:
    # What a listing query shows must be exactly what a route allows (ADR 0014).
    allowed = decide(principal, permission, target).allowed
    visible = scope_for(principal, permission).contains(target.organisation, target.group)

    assert allowed == visible


def test_permissions_for_lists_rights_with_their_scope() -> None:
    rights = dict(permissions_for(GROUP))

    assert rights[Permission.SCHEDULE_MANAGE] == Scope(groups=frozenset({"demo"}))
    assert rights[Permission.DASHBOARD_VIEW] is None  # global: no scope
    assert Permission.GROUP_EDIT not in rights
    assert permissions_for(NOBODY) == []


def test_principal_repr_hides_the_display_name() -> None:
    assert "Super" not in repr(SUPER)


def test_citizen_actions_are_part_of_the_matrix() -> None:
    from app.domain.authorization import CITIZEN_ACTIONS

    conditions = {action.key: action.condition for action in CITIZEN_ACTIONS}

    assert conditions == {
        "citizen.availability_view": "public",
        "citizen.book": "public",
        "citizen.booking_view": "management_link",
        "citizen.booking_change": "management_link_before_deadline",
        "citizen.booking_cancel": "management_link_before_deadline",
    }
