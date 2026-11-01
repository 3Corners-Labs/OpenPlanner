"""The authorisation matrix (ADR 0014): decide() and scope_for() must always agree."""

import itertools

import pytest

from app.domain.authorization import (
    API_SCOPES,
    LEVELS,
    LOCKED_PERMISSIONS,
    MAINTENANCE_WRITABLE,
    MATRIX,
    PII_PERMISSIONS,
    REACH,
    ApiScope,
    ClientGrant,
    Decision,
    Level,
    Permission,
    Principal,
    Reach,
    Reason,
    Role,
    Scope,
    Target,
    decide,
    decide_client,
    decide_in_maintenance,
    permissions_for,
    role_keys,
    scope_for,
)
from tests.support.principals import principal

SUPER = principal("s", "Super", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG = principal("o", "Org", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP = principal("g", "Group", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
AUDITOR = principal("a", "Auditor", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())
NOBODY = principal("n", "Nobody", frozenset(), frozenset({"demo"}), frozenset({"demo"}))
MIXED = principal(
    "m",
    "Mixed",
    frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
    frozenset({"andere"}),
    frozenset({"demo"}),
)

EVERY_ROLE = principal(
    "e",
    "Every role",
    frozenset(Role),
    frozenset({"demo"}),
    frozenset({"demo"}),
)
"""The dev realm's ``super``: the superuser reach wins, the other roles add nothing."""

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
        Permission.AUDIT_EXPORT,  # ADR 0040
        Permission.ORGANISATION_VIEW,  # ADR 0047 step 3: the own organisations' pages
        Permission.HOLIDAYS_ASSIGN,  # ADR 0025
        Permission.GROUP_CREATE,
        Permission.GROUP_DELETE,  # ADR 0084: archive what they create
        Permission.GROUP_VIEW,
        Permission.GROUP_EDIT,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.BOOKINGS_VIEW,
        Permission.BOOKINGS_MANAGE,  # ADR 0044
        Permission.CLOSURE_MANAGE,
        Permission.ORGANISATION_CLOSURE,
        Permission.RETENTION_VIEW,
        Permission.STAFF_VIEW,  # ADR 0046
        Permission.EMBEDDING_MANAGE,  # ADR 0035
        Permission.QUEUE_WORK,  # ADR 0043
        Permission.SETTINGS_ORGANISATION_MANAGE,  # ADR 0042: the own organisation's settings
        Permission.TEXTS_ORGANISATION_MANAGE,  # ADR 0004: the organisation's own wording
        Permission.IDP_MAPPING_VIEW,  # ADR 0049
        Permission.IDP_MAPPING_MANAGE,  # ADR 0049
        Permission.IDENTITY_PROVIDERS_VIEW,  # ADR 0083
    }
    assert MATRIX[Role.GROUP_ADMIN] == {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.AUDIT_VIEW,  # ADR 0031: the log of the own groups only
        Permission.GROUP_VIEW,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.BOOKINGS_VIEW,
        Permission.BOOKINGS_MANAGE,  # ADR 0044: the team's own planner
        Permission.CLOSURE_MANAGE,
        Permission.STAFF_VIEW,  # ADR 0046: read-only, the own groups
        Permission.QUEUE_WORK,  # ADR 0043
    }
    # ADR 0043: the agent works the own queue of its groups and reads its own rights.
    assert MATRIX[Role.AGENT] == {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.GROUP_VIEW,
        Permission.QUEUE_WORK,
    }
    # ADR 0039: compliance and privacy, read-only, never personal data.
    assert MATRIX[Role.AUDITOR] == {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,  # ADR 0040: the same log as a file
        Permission.ORGANISATION_VIEW,  # ADR 0047 step 3: the organisations' pages, read-only
        Permission.GROUP_VIEW,
        Permission.RETENTION_VIEW,
        Permission.STAFF_VIEW,  # ADR 0046
        Permission.SETTINGS_PLATFORM_VIEW,  # ADR 0042: system status, read-only
        Permission.IDP_MAPPING_VIEW,  # ADR 0049
        Permission.IDENTITY_PROVIDERS_VIEW,  # ADR 0083
        Permission.LOGGING_VIEW,  # ADR 0085: where the log goes, no credential
    }


def test_the_auditor_never_holds_personal_data_or_a_change() -> None:
    read_only = {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,
        Permission.ORGANISATION_VIEW,  # ADR 0047 step 3: the organisation's page, read-only
        Permission.GROUP_VIEW,
        Permission.RETENTION_VIEW,
        Permission.STAFF_VIEW,  # ADR 0046
        Permission.SETTINGS_PLATFORM_VIEW,  # ADR 0042
        Permission.IDP_MAPPING_VIEW,  # ADR 0049
        Permission.IDENTITY_PROVIDERS_VIEW,  # ADR 0083
        Permission.LOGGING_VIEW,  # ADR 0085: where the log goes, no credential
    }

    assert not MATRIX[Role.AUDITOR] & PII_PERMISSIONS
    assert MATRIX[Role.AUDITOR] <= read_only


def test_locked_permissions_belong_to_the_superuser_only() -> None:
    assert {
        Permission.ORGANISATION_MANAGE,
        Permission.ORGANISATION_DELETE,  # ADR 0084: superuser only, like managing
        Permission.HOLIDAYS_MANAGE,
        Permission.INTEGRATIONS_MANAGE,
        Permission.API_CLIENTS_MANAGE,
        Permission.SYSTEM_UPDATE,
        Permission.ROLES_MANAGE,  # ADR 0039 step 3
        Permission.SYSTEM_MAINTENANCE,
        Permission.DIAGNOSTICS_MANAGE,  # ADR 0040: product owner, 2026-10-29
        Permission.SETTINGS_PLATFORM_MANAGE,  # ADR 0042 §5
        Permission.SETTINGS_LOGIN_MANAGE,  # ADR 0042 §5: never grantable to another role
        Permission.IDENTITY_PROVIDERS_MANAGE,  # ADR 0083 point 4
        Permission.LOGGING_MANAGE,  # ADR 0085: credentials of a log platform
    } == LOCKED_PERMISSIONS
    for permission in LOCKED_PERMISSIONS:
        assert [role for role in Role if permission in MATRIX[role]] == [Role.SUPERUSER]


def test_bookings_and_the_queue_are_the_permissions_that_show_personal_data() -> None:
    assert {
        Permission.BOOKINGS_VIEW,
        Permission.BOOKINGS_MANAGE,
        Permission.QUEUE_WORK,
    } == PII_PERMISSIONS


def test_every_role_has_a_reach_and_only_the_superuser_reaches_everything() -> None:
    assert REACH == {
        Role.SUPERUSER: Reach.EVERYTHING,
        Role.ORG_ADMIN: Reach.ORGANISATIONS,
        Role.GROUP_ADMIN: Reach.GROUPS,
        Role.AUDITOR: Reach.ORGANISATIONS,
        Role.AGENT: Reach.GROUPS,
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
        (ORG, Permission.AUDIT_VIEW, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (GROUP, Permission.SCHEDULE_MANAGE, OWN_GROUP, Decision(True, Reason.GROUP)),
        (GROUP, Permission.SCHEDULE_MANAGE, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (GROUP, Permission.GROUP_EDIT, OWN_GROUP, Decision(False, Reason.NOT_PERMITTED)),
        (GROUP, Permission.AUDIT_VIEW, OWN_GROUP, Decision(True, Reason.GROUP)),
        (GROUP, Permission.AUDIT_VIEW, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
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
        (AUDITOR, Permission.AUDIT_VIEW, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (AUDITOR, Permission.AUDIT_VIEW, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (AUDITOR, Permission.GROUP_VIEW, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (AUDITOR, Permission.BOOKINGS_VIEW, OWN_GROUP, Decision(False, Reason.NOT_PERMITTED)),
        (AUDITOR, Permission.GROUP_EDIT, OWN_GROUP, Decision(False, Reason.NOT_PERMITTED)),
        (AUDITOR, Permission.MATRIX_VIEW, Target.everything(), Decision(True, Reason.ROLE)),
        (AUDITOR, Permission.RETENTION_VIEW, OWN_ORG, Decision(True, Reason.ORGANISATION)),
        (ORG, Permission.RETENTION_VIEW, OTHER_ORG, Decision(False, Reason.OUT_OF_SCOPE)),
        (GROUP, Permission.RETENTION_VIEW, OWN_ORG, Decision(False, Reason.NOT_PERMITTED)),
        # ADR 0046: staff of the own organisation or group.
        (AUDITOR, Permission.STAFF_VIEW, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (GROUP, Permission.STAFF_VIEW, OWN_GROUP, Decision(True, Reason.GROUP)),
        (GROUP, Permission.STAFF_VIEW, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        # ADR 0035: the organisation decides where its groups may be embedded.
        (ORG, Permission.EMBEDDING_MANAGE, OWN_ORG, Decision(True, Reason.ORGANISATION)),
        (ORG, Permission.EMBEDDING_MANAGE, OTHER_ORG, Decision(False, Reason.OUT_OF_SCOPE)),
        (GROUP, Permission.EMBEDDING_MANAGE, OWN_ORG, Decision(False, Reason.NOT_PERMITTED)),
        (AUDITOR, Permission.EMBEDDING_MANAGE, OWN_ORG, Decision(False, Reason.NOT_PERMITTED)),
        # ADR 0042 §5: settings of the platform and of the own organisation.
        (
            AUDITOR,
            Permission.SETTINGS_PLATFORM_VIEW,
            Target.everything(),
            Decision(True, Reason.ROLE),
        ),
        (
            ORG,
            Permission.SETTINGS_PLATFORM_VIEW,
            Target.everything(),
            Decision(False, Reason.NOT_PERMITTED),
        ),
        (
            AUDITOR,
            Permission.SETTINGS_PLATFORM_MANAGE,
            Target.everything(),
            Decision(False, Reason.NOT_PERMITTED),
        ),
        (
            SUPER,
            Permission.SETTINGS_LOGIN_MANAGE,
            Target.everything(),
            Decision(True, Reason.SUPERUSER),
        ),
        (
            ORG,
            Permission.SETTINGS_ORGANISATION_MANAGE,
            OWN_ORG,
            Decision(True, Reason.ORGANISATION),
        ),
        (
            ORG,
            Permission.SETTINGS_ORGANISATION_MANAGE,
            OTHER_ORG,
            Decision(False, Reason.OUT_OF_SCOPE),
        ),
        (
            GROUP,
            Permission.SETTINGS_ORGANISATION_MANAGE,
            OWN_ORG,
            Decision(False, Reason.NOT_PERMITTED),
        ),
        (ORG, Permission.TEXTS_ORGANISATION_MANAGE, OWN_ORG, Decision(True, Reason.ORGANISATION)),
        (
            ORG,
            Permission.TEXTS_ORGANISATION_MANAGE,
            OTHER_ORG,
            Decision(False, Reason.OUT_OF_SCOPE),
        ),
        (
            GROUP,
            Permission.TEXTS_ORGANISATION_MANAGE,
            OWN_ORG,
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
    assert scope_for(GROUP, Permission.GROUP_VIEW) == Scope(groups=frozenset({("demo", "demo")}))
    assert scope_for(GROUP, Permission.GROUP_EDIT).is_empty
    assert scope_for(NOBODY, Permission.GROUP_VIEW).is_empty


def test_audit_scope_is_exactly_the_own_level() -> None:
    # ADR 0031: a group admin's log holds its groups only, never the organisation's own
    # events; an organisation admin's log holds no global events.
    assert scope_for(GROUP, Permission.AUDIT_VIEW) == Scope(groups=frozenset({("demo", "demo")}))
    assert not scope_for(GROUP, Permission.AUDIT_VIEW).contains("demo", None)
    assert scope_for(ORG, Permission.AUDIT_VIEW).contains("demo", None)
    assert not scope_for(ORG, Permission.AUDIT_VIEW).contains(None, None)
    assert scope_for(SUPER, Permission.AUDIT_VIEW).contains(None, None)
    # ADR 0039: the auditor reads the log exactly as the organisation admin does.
    assert scope_for(AUDITOR, Permission.AUDIT_VIEW) == scope_for(ORG, Permission.AUDIT_VIEW)


def test_retention_scope_is_the_organisation() -> None:
    # ADR 0039: counts per group of the own organisation; a group admin has none.
    assert scope_for(ORG, Permission.RETENTION_VIEW) == Scope(organisations=frozenset({"demo"}))
    auditor = scope_for(AUDITOR, Permission.RETENTION_VIEW)
    assert auditor == scope_for(ORG, Permission.RETENTION_VIEW)
    assert scope_for(GROUP, Permission.RETENTION_VIEW).is_empty
    assert scope_for(SUPER, Permission.RETENTION_VIEW) == Scope(everything=True)


def test_staff_scope_is_the_organisation_or_the_groups() -> None:
    # ADR 0046: a group admin sees the staff of their own groups.
    assert scope_for(ORG, Permission.STAFF_VIEW) == Scope(organisations=frozenset({"demo"}))
    assert scope_for(GROUP, Permission.STAFF_VIEW) == Scope(groups=frozenset({("demo", "demo")}))
    assert scope_for(SUPER, Permission.STAFF_VIEW) == Scope(everything=True)


def test_scope_for_rejects_global_permissions() -> None:
    with pytest.raises(ValueError, match="global"):
        scope_for(SUPER, Permission.DASHBOARD_VIEW)


def test_scope_contains() -> None:
    scope = Scope(organisations=frozenset({"demo"}), groups=frozenset({("andere", "x")}))

    assert scope.contains("demo", "anything")
    assert scope.contains("andere", "x")
    assert not scope.contains("andere", "y")
    assert not scope.contains("elders", "x")  # ADR 0049: a group only with its organisation
    assert Scope(everything=True).contains("a", "b")
    assert not Scope().contains("a", "b")


PRINCIPALS = [SUPER, ORG, GROUP, AUDITOR, NOBODY, MIXED, EVERY_ROLE]
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


@pytest.mark.parametrize("permission", list(Permission))
def test_holding_every_role_decides_and_lists_like_the_superuser(permission: Permission) -> None:
    level = LEVELS[permission]
    target = {
        Level.GLOBAL: Target.everything(),
        Level.ORGANISATION: OTHER_ORG,
        Level.GROUP: OTHER_GROUP,
    }[level]

    assert decide(EVERY_ROLE, permission, target) == decide(SUPER, permission, target)
    assert decide(EVERY_ROLE, permission, target).reason is Reason.SUPERUSER
    if level is not Level.GLOBAL:
        assert scope_for(EVERY_ROLE, permission) == Scope(everything=True)
    assert role_keys(EVERY_ROLE) == sorted(Role)


def test_permissions_for_lists_rights_with_their_scope() -> None:
    rights = dict(permissions_for(GROUP))

    assert rights[Permission.SCHEDULE_MANAGE] == Scope(groups=frozenset({("demo", "demo")}))
    assert rights[Permission.DASHBOARD_VIEW] is None  # global: no scope
    assert Permission.GROUP_EDIT not in rights
    assert permissions_for(NOBODY) == []


def test_principal_repr_hides_the_display_name() -> None:
    assert "Super" not in repr(SUPER)


def test_participant_actions_are_part_of_the_matrix() -> None:
    from app.domain.authorization import PARTICIPANT_ACTIONS

    conditions = {action.key: action.condition for action in PARTICIPANT_ACTIONS}

    assert conditions == {
        "participant.availability_view": "public",
        "participant.book": "public",
        "participant.booking_view": "management_link",
        "participant.booking_change": "management_link_before_deadline",
        "participant.booking_cancel": "management_link_before_deadline",
        "participant.suspended_booking_change": "management_link_while_suspended",
    }


# --- API clients (ADR 0023) ---------------------------------------------------------------

READ = ApiScope.AVAILABILITY_READ
WHOLE_ORG = ClientGrant("opc_a", "demo", frozenset(), frozenset({READ}))
ONE_GROUP = ClientGrant("opc_b", "demo", frozenset({"demo"}), frozenset({READ}))
NO_SCOPES = ClientGrant("opc_c", "demo", frozenset(), frozenset())


def test_api_clients_are_managed_by_the_superuser_only() -> None:
    assert LEVELS[Permission.API_CLIENTS_MANAGE] is Level.GLOBAL
    holders = [role for role in Role if Permission.API_CLIENTS_MANAGE in MATRIX[role]]
    assert holders == [Role.SUPERUSER]


def test_every_api_scope_has_a_level() -> None:
    assert set(API_SCOPES) == set(ApiScope)


@pytest.mark.parametrize(
    ("grant", "target", "expected"),
    [
        (WHOLE_ORG, OWN_GROUP, Decision(True, Reason.ORGANISATION)),
        (WHOLE_ORG, Target.for_group("andere-groep", "demo"), Decision(True, Reason.ORGANISATION)),
        (WHOLE_ORG, OTHER_GROUP, Decision(False, Reason.OUT_OF_SCOPE)),
        (ONE_GROUP, OWN_GROUP, Decision(True, Reason.GROUP)),
        (ONE_GROUP, Target.for_group("andere-groep", "demo"), Decision(False, Reason.OUT_OF_SCOPE)),
        # A group slug of the client in another organisation still does not match.
        (ONE_GROUP, Target.for_group("demo", "andere"), Decision(False, Reason.OUT_OF_SCOPE)),
        (NO_SCOPES, OWN_GROUP, Decision(False, Reason.NOT_PERMITTED)),
    ],
)
def test_decide_client(grant: ClientGrant, target: Target, expected: Decision) -> None:
    assert decide_client(grant, READ, target) == expected


def test_decide_client_refuses_a_target_of_the_wrong_level() -> None:
    with pytest.raises(ValueError, match="group level target"):
        decide_client(WHOLE_ORG, READ, OWN_ORG)


TRANSFER = ApiScope.CONFIGURATION_TRANSFER
TRANSFER_CLIENT = ClientGrant("opc_d", "andere", frozenset({"andere-team"}), frozenset({TRANSFER}))


def test_a_global_scope_acts_on_the_whole_installation() -> None:
    # ADR 0089 point 7: the organisation that registered the client and its groups play no part.
    assert API_SCOPES[TRANSFER] is Level.GLOBAL
    assert decide_client(TRANSFER_CLIENT, TRANSFER, Target.everything()) == Decision(
        True, Reason.ROLE
    )
    assert decide_client(WHOLE_ORG, TRANSFER, Target.everything()) == Decision(
        False, Reason.NOT_PERMITTED
    )
    with pytest.raises(ValueError, match="global level target"):
        decide_client(TRANSFER_CLIENT, TRANSFER, OWN_GROUP)


def test_maintenance_writable_is_the_adr_list() -> None:
    # ADR 0038 §3, written out by hand: integrations, updates and the switch itself.
    assert {
        Permission.INTEGRATIONS_MANAGE,
        Permission.SYSTEM_UPDATE,
        Permission.SYSTEM_MAINTENANCE,
    } == MAINTENANCE_WRITABLE


@pytest.mark.parametrize("permission", sorted(MAINTENANCE_WRITABLE))
def test_maintenance_lets_the_superuser_change_what_is_writable(permission: Permission) -> None:
    assert decide_in_maintenance(SUPER, permission) == Decision(True, Reason.SUPERUSER)


@pytest.mark.parametrize("permission", sorted(set(Permission) - MAINTENANCE_WRITABLE))
def test_maintenance_freezes_everything_else_also_for_the_superuser(
    permission: Permission,
) -> None:
    assert decide_in_maintenance(SUPER, permission) == Decision(False, Reason.MAINTENANCE)


@pytest.mark.parametrize("principal", [ORG, GROUP, AUDITOR, NOBODY, MIXED])
@pytest.mark.parametrize("permission", sorted(Permission))
def test_maintenance_freezes_every_other_role(principal: Principal, permission: Permission) -> None:
    assert not decide_in_maintenance(principal, permission).allowed
    assert not decide_in_maintenance(principal, permission, maintenance_safe=True).allowed


def test_a_maintenance_safe_route_is_open_to_the_superuser_only() -> None:
    allowed = decide_in_maintenance(SUPER, Permission.GROUP_EDIT, maintenance_safe=True)

    assert allowed == Decision(True, Reason.SUPERUSER)
    assert not decide_in_maintenance(ORG, Permission.GROUP_EDIT, maintenance_safe=True).allowed
