"""Custom roles within fixed bounds (ADR 0039 step 3): the rules, not an expectation table.

Built-in roles have their hand-written table in ``test_authorization.py``. Custom roles are
data, so these tests check the bounds for every definition: whatever ``validate_role``
accepts holds no locked permission and nothing above its reach, a copy of the auditor never
shows personal data or changes anything, and ``decide`` never allows more than the definition.
"""

import itertools
import random

import pytest

from app.domain.authorization import (
    EVERY_ADMIN,
    LEVELS,
    LOCKED_PERMISSIONS,
    MATRIX,
    PII_PERMISSIONS,
    READ_PERMISSIONS,
    REQUIRES,
    TEMPLATES,
    CustomRole,
    Level,
    Permission,
    Principal,
    Reach,
    Reason,
    Role,
    RoleCatalogue,
    RoleRule,
    Scope,
    Target,
    Violation,
    decide,
    delegable,
    holds_a_role,
    permissions_for,
    role_keys,
    scope_for,
    validate_role,
)

OWN_GROUP = Target.for_group("demo", "demo")
OTHER_GROUP = Target.for_group("toeslagen", "andere")
OWN_ORG = Target.for_organisation("demo")
OTHER_ORG = Target.for_organisation("andere")
TARGETS = {
    Level.GLOBAL: [Target.everything()],
    Level.ORGANISATION: [OWN_ORG, OTHER_ORG],
    Level.GROUP: [OWN_GROUP, OTHER_GROUP],
}


def custom(template: Role, permissions: frozenset[Permission], role_id: int = 1) -> CustomRole:
    """A stored custom role with the given template and permissions."""
    return CustomRole(
        id=role_id,
        template=template,
        permissions=permissions,
        claim_value=f"openplanner-custom-{role_id}",
        name_nl="Teamleider",
        name_en="Team lead",
        version=1,
    )


def holder(role: CustomRole) -> Principal:
    """A principal holding only ``role``, in organisation demo and group demo."""
    base = Principal("c", "Carla Custom", frozenset(), frozenset({"demo"}), frozenset({"demo"}))
    return RoleCatalogue((role,)).holding(
        Principal(
            base.subject,
            base.display_name,
            base.roles,
            base.organisations,
            base.groups,
            role_claims=frozenset({role.claim_value}),
        )
    )


def random_definitions(count: int) -> list[tuple[Role, frozenset[Permission]]]:
    """Seeded random permission sets per template: a property test without a new dependency."""
    rng = random.Random(39)  # noqa: S311 - test data, not security
    every = list(Permission)
    return [
        (rng.choice(TEMPLATES), frozenset(p for p in every if rng.random() < 0.5))
        for _ in range(count)
    ]


# The bounds themselves (code, changed only by PR).


def test_roles_manage_is_a_new_locked_global_permission() -> None:
    assert LEVELS[Permission.ROLES_MANAGE] is Level.GLOBAL
    assert Permission.ROLES_MANAGE in LOCKED_PERMISSIONS
    assert [role for role in Role if Permission.ROLES_MANAGE in MATRIX[role]] == [Role.SUPERUSER]


def test_requirements_follow_the_adr() -> None:
    needs_view = {
        Permission.GROUP_EDIT,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.CLOSURE_MANAGE,
    }
    for permission in needs_view:
        assert REQUIRES[permission] == {Permission.GROUP_VIEW}
    assert REQUIRES[Permission.ORGANISATION_CLOSURE] == {Permission.CLOSURE_MANAGE}


def test_every_built_in_role_meets_its_own_requirements() -> None:
    for role in Role:
        for permission in MATRIX[role]:
            assert REQUIRES.get(permission, frozenset()) <= MATRIX[role], (role, permission)


def test_every_view_permission_is_a_read_permission_and_nothing_else_is() -> None:
    # A new ``*.view`` permission must be classified here before a read-only copy can hold it.
    # The audit export only downloads what ``audit.view`` shows, so it reads too.
    views = {p for p in Permission if p.value.endswith(".view")}
    assert views | {Permission.AUDIT_EXPORT} == READ_PERMISSIONS


def test_custom_roles_copy_the_three_templates_but_never_the_superuser() -> None:
    assert set(TEMPLATES) == {Role.ORG_ADMIN, Role.GROUP_ADMIN, Role.AUDITOR}


def test_every_template_is_valid_as_it_is() -> None:
    for template in TEMPLATES:
        assert validate_role(template, MATRIX[template]) == []
        assert MATRIX[template] <= delegable(template)


def test_delegable_permissions_are_within_the_reach() -> None:
    group_reach = delegable(Role.GROUP_ADMIN)
    assert not {p for p in group_reach if LEVELS[p] is Level.ORGANISATION}
    assert Permission.GROUP_EDIT in group_reach  # a group admin's copy may get more
    assert Permission.GROUP_CREATE in delegable(Role.ORG_ADMIN)
    assert delegable(Role.AUDITOR) == READ_PERMISSIONS - PII_PERMISSIONS
    for template in TEMPLATES:
        assert not delegable(template) & LOCKED_PERMISSIONS
        assert Permission.BRANDING_MANAGE not in delegable(template)  # ADR 0039 B3, until O5


# validate_role: each refusal.


@pytest.mark.parametrize(
    ("template", "extra", "expected"),
    [
        (Role.ORG_ADMIN, Permission.INTEGRATIONS_MANAGE, RoleRule.LOCKED),
        (Role.ORG_ADMIN, Permission.ROLES_MANAGE, RoleRule.LOCKED),
        (Role.ORG_ADMIN, Permission.BRANDING_MANAGE, RoleRule.LOCKED),
        (Role.GROUP_ADMIN, Permission.GROUP_CREATE, RoleRule.ABOVE_REACH),
        (Role.GROUP_ADMIN, Permission.RETENTION_VIEW, RoleRule.ABOVE_REACH),
        (Role.AUDITOR, Permission.BOOKINGS_VIEW, RoleRule.PII),
        (Role.AUDITOR, Permission.GROUP_EDIT, RoleRule.WRITE),
    ],
)
def test_validate_role_refuses_a_permission_outside_the_bounds(
    template: Role, extra: Permission, expected: RoleRule
) -> None:
    assert Violation(expected, extra) in validate_role(template, MATRIX[template] | {extra})


def test_validate_role_refuses_a_missing_requirement() -> None:
    permissions = (MATRIX[Role.GROUP_ADMIN] - {Permission.GROUP_VIEW}) | {Permission.GROUP_VIEW}
    assert validate_role(Role.GROUP_ADMIN, permissions) == []

    without_view = EVERY_ADMIN | {Permission.SCHEDULE_MANAGE}
    assert validate_role(Role.GROUP_ADMIN, without_view) == [
        Violation(RoleRule.REQUIREMENT, Permission.SCHEDULE_MANAGE)
    ]
    closure = EVERY_ADMIN | {Permission.GROUP_VIEW, Permission.ORGANISATION_CLOSURE}
    assert validate_role(Role.ORG_ADMIN, closure) == [
        Violation(RoleRule.REQUIREMENT, Permission.ORGANISATION_CLOSURE)
    ]


def test_validate_role_refuses_a_role_without_the_basics() -> None:
    assert validate_role(Role.ORG_ADMIN, frozenset({Permission.GROUP_VIEW})) == [
        Violation(RoleRule.BASICS, Permission.DASHBOARD_VIEW),
        Violation(RoleRule.BASICS, Permission.MATRIX_VIEW),
        Violation(RoleRule.BASICS, Permission.RIGHTS_VIEW),
    ]


def test_validate_role_refuses_the_superuser_as_a_template() -> None:
    assert validate_role(Role.SUPERUSER, MATRIX[Role.ORG_ADMIN]) == [
        Violation(RoleRule.TEMPLATE, None)
    ]


# Properties over many definitions.

DEFINITIONS = random_definitions(1500)


def test_whatever_validate_role_accepts_stays_inside_its_bounds() -> None:
    accepted = 0
    for template, permissions in DEFINITIONS + [(t, MATRIX[t]) for t in TEMPLATES]:
        if validate_role(template, permissions):
            continue
        accepted += 1
        assert not permissions & LOCKED_PERMISSIONS
        assert permissions <= delegable(template)
        assert EVERY_ADMIN <= permissions
        if template is Role.GROUP_ADMIN:
            assert all(LEVELS[p] is not Level.ORGANISATION for p in permissions)
        if template is Role.AUDITOR:
            assert not permissions & PII_PERMISSIONS
            assert permissions <= READ_PERMISSIONS
    assert accepted >= len(TEMPLATES)


def closed(template: Role, permissions: frozenset[Permission]) -> frozenset[Permission]:
    """Clip ``permissions`` to the bounds and add the basics and every requirement."""
    result = (permissions & delegable(template)) | EVERY_ADMIN
    while (more := result | {n for p in result for n in REQUIRES.get(p, frozenset())}) != result:
        result = more
    return result


def test_clipping_and_completing_any_set_gives_a_valid_role() -> None:
    # The editor can always be filled in validly, and the random sample stays meaningful.
    for template, permissions in DEFINITIONS:
        assert validate_role(template, closed(template, permissions)) == [], template


@pytest.mark.parametrize(("template", "permissions"), DEFINITIONS[:300])
def test_decide_never_allows_more_than_the_definition(
    template: Role, permissions: frozenset[Permission]
) -> None:
    # Even a definition that skipped validation (a tampered row) is clipped to its bounds.
    role = custom(template, permissions)
    principal = holder(role)
    for permission in Permission:
        for target in TARGETS[LEVELS[permission]]:
            decision = decide(principal, permission, target)
            if decision.allowed:
                assert permission in permissions & delegable(template)
                assert decision.reason is not Reason.SUPERUSER
                assert target not in (OTHER_GROUP, OTHER_ORG)


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_copy_of_the_template_decides_exactly_like_the_template(template: Role) -> None:
    role = custom(template, MATRIX[template])
    copy = holder(role)
    original = Principal("t", "T", frozenset({template}), copy.organisations, copy.groups)
    for permission in Permission:
        for target in TARGETS[LEVELS[permission]]:
            assert (
                decide(copy, permission, target).allowed
                == decide(original, permission, target).allowed
            )
            if LEVELS[permission] is not Level.GLOBAL:
                assert scope_for(copy, permission) == scope_for(original, permission)


def test_an_auditor_copy_never_reaches_personal_data_even_if_stored_with_it() -> None:
    role = custom(Role.AUDITOR, frozenset(Permission))
    principal = holder(role)
    assert not decide(principal, Permission.BOOKINGS_VIEW, OWN_GROUP).allowed
    assert not decide(principal, Permission.GROUP_EDIT, OWN_GROUP).allowed
    assert scope_for(principal, Permission.BOOKINGS_VIEW).is_empty


def test_group_reach_uses_the_group_claim_and_organisation_reach_the_organisation_claim() -> None:
    view = EVERY_ADMIN | {Permission.GROUP_VIEW}
    group_role = holder(custom(Role.GROUP_ADMIN, view))
    org_role = holder(custom(Role.ORG_ADMIN, view))
    assert scope_for(group_role, Permission.GROUP_VIEW) == Scope(groups=frozenset({"demo"}))
    assert scope_for(org_role, Permission.GROUP_VIEW) == Scope(organisations=frozenset({"demo"}))


# Linking to the identity provider.


def test_a_role_is_held_only_through_its_claim_value() -> None:
    role = custom(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN])
    catalogue = RoleCatalogue((role,))
    none = Principal("x", "X", frozenset(), frozenset(), frozenset({"demo"}))
    other = Principal(
        "x", "X", frozenset(), frozenset(), frozenset({"demo"}), role_claims=frozenset({"nope"})
    )
    assert catalogue.holding(none).custom_roles == frozenset()
    assert catalogue.holding(other).custom_roles == frozenset()
    assert not holds_a_role(catalogue.holding(other))
    assert decide(catalogue.holding(other), Permission.DASHBOARD_VIEW, Target.everything()) == (
        decide(none, Permission.DASHBOARD_VIEW, Target.everything())
    )
    assert holder(role).custom_roles == frozenset({role})
    assert holds_a_role(holder(role))


def test_a_deleted_role_leaves_its_holders_without_a_role() -> None:
    role = custom(Role.ORG_ADMIN, MATRIX[Role.ORG_ADMIN])
    principal = RoleCatalogue(()).holding(holder(role))
    assert decide(principal, Permission.DASHBOARD_VIEW, Target.everything()).reason is (
        Reason.NO_ROLE
    )
    assert permissions_for(principal) == []


def test_role_keys_name_built_in_and_custom_roles() -> None:
    role = custom(Role.ORG_ADMIN, MATRIX[Role.ORG_ADMIN], role_id=7)
    principal = holder(role)
    mixed = Principal(
        principal.subject,
        principal.display_name,
        frozenset({Role.AUDITOR}),
        principal.organisations,
        principal.groups,
        role_claims=principal.role_claims,
        custom_roles=principal.custom_roles,
    )
    assert role.key == "custom.7"
    assert role_keys(mixed) == ["auditor", "custom.7"]


def test_a_custom_role_has_the_reach_of_its_template() -> None:
    assert custom(Role.ORG_ADMIN, frozenset()).reach is Reach.ORGANISATIONS
    assert custom(Role.AUDITOR, frozenset()).reach is Reach.ORGANISATIONS
    assert custom(Role.GROUP_ADMIN, frozenset()).reach is Reach.GROUPS


def test_custom_role_names_per_locale() -> None:
    role = custom(Role.ORG_ADMIN, frozenset())
    assert role.name("nl") == "Teamleider"
    assert role.name("en") == "Team lead"
    assert role.name("de") == "Team lead"


def test_two_custom_roles_add_up_within_their_own_reach() -> None:
    org_view = custom(Role.ORG_ADMIN, EVERY_ADMIN | {Permission.GROUP_VIEW}, 1)
    group_edit = custom(
        Role.GROUP_ADMIN, EVERY_ADMIN | {Permission.GROUP_VIEW, Permission.GROUP_EDIT}, 2
    )
    principal = RoleCatalogue((org_view, group_edit)).holding(
        Principal(
            "c",
            "C",
            frozenset(),
            frozenset({"andere"}),
            frozenset({"demo"}),
            role_claims=frozenset({org_view.claim_value, group_edit.claim_value}),
        )
    )
    assert decide(principal, Permission.GROUP_VIEW, OTHER_GROUP).allowed
    assert decide(principal, Permission.GROUP_EDIT, OWN_GROUP).allowed
    assert not decide(principal, Permission.GROUP_EDIT, OTHER_GROUP).allowed


def test_principal_repr_hides_the_display_name() -> None:
    principal = holder(custom(Role.ORG_ADMIN, MATRIX[Role.ORG_ADMIN]))
    assert "Carla" not in repr(principal)


def test_combinations_of_templates_never_reach_everything() -> None:
    for first, second in itertools.combinations(TEMPLATES, 2):
        principal = RoleCatalogue(
            (custom(first, MATRIX[first], 1), custom(second, MATRIX[second], 2))
        ).holding(
            Principal(
                "c",
                "C",
                frozenset(),
                frozenset(),
                frozenset(),
                role_claims=frozenset({"openplanner-custom-1", "openplanner-custom-2"}),
            )
        )
        assert not scope_for(principal, Permission.GROUP_VIEW).everything
