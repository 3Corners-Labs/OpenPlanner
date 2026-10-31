"""Entitlements (ADR 0049): parsing, the grant model and the abuse cases A1–A8, A13, A14."""

import random
import string

import pytest

from app.domain.authorization import (
    EVERY_ADMIN,
    LEVELS,
    MATRIX,
    CustomRole,
    Grant,
    Permission,
    Principal,
    Reach,
    Role,
    RoleCatalogue,
    Scope,
    Target,
    decide,
    scope_for,
)
from app.domain.entitlements import (
    ClaimMode,
    Directory,
    GroupRef,
    LoginClaims,
    OrganisationRef,
    RefusalReason,
    entitlement_value,
    is_ambiguous,
    legacy_grants,
    mismatch_detail,
    parse_entitlements,
    recognised_values,
    resolve,
)
from app.domain.settings_registry import CLAIM_MODES
from app.domain.staff import RefusedEntitlement, StaffMember
from tests.support.domain import NOW

READ_ONLY = CustomRole(
    3, Role.GROUP_ADMIN, EVERY_ADMIN | {Permission.GROUP_VIEW}, "op-readonly", "Lezer", "Reader"
)
CATALOGUE = RoleCatalogue((READ_ONLY,))
DIRECTORY = Directory.of(
    CATALOGUE,
    [OrganisationRef("gem-a", "gem-a"), OrganisationRef("gem-b", "idp-b")],
    [
        GroupRef("g1", "g1", "gem-a"),
        GroupRef("g2", "g2", "gem-a"),
        GroupRef("burgerzaken", "bz", "gem-b"),
    ],
)
PREFIX = "openplanner"


def parsed(*values: str) -> tuple[frozenset[Grant], dict[str, str]]:
    found = parse_entitlements(values, PREFIX, DIRECTORY)
    return found.grants, {r.value: r.reason.value for r in found.refused}


def admin(*grants: Grant) -> Principal:
    return CATALOGUE.holding(Principal("s", "S", frozenset(grants)))


# --- the grammar --------------------------------------------------------------------------


def test_values_become_grants_by_idp_value_not_by_slug() -> None:
    grants, refused = parsed(
        "openplanner:superuser",
        "openplanner:org_admin:idp-b",
        "openplanner:group_admin:idp-b/bz",
        "openplanner:custom.3:gem-a/g2",
    )

    assert grants == {
        Grant("superuser"),
        Grant("org_admin", "gem-b"),
        Grant("group_admin", "gem-b", "burgerzaken"),
        Grant("custom.3", "gem-a", "g2"),
    }
    assert refused == {}


def test_a4_a_group_of_another_organisation_is_refused() -> None:
    grants, refused = parsed("openplanner:group_admin:idp-b/g1")

    assert grants == frozenset()
    assert refused == {"openplanner:group_admin:idp-b/g1": "group_not_in_organisation"}


def test_a5_the_superuser_takes_no_scope() -> None:
    grants, refused = parsed("openplanner:superuser:gem-a", "openplanner:superuser:gem-a/g1")

    assert grants == frozenset()
    assert set(refused.values()) == {"scope_not_allowed_for_role"}


def test_a6_the_reach_decides_the_form() -> None:
    grants, refused = parsed("openplanner:org_admin:gem-a/g1", "openplanner:group_admin:gem-a")

    assert grants == frozenset()
    assert set(refused.values()) == {"scope_not_allowed_for_role"}


def test_unknown_role_organisation_and_group_are_refused_with_their_reason() -> None:
    _, refused = parsed(
        "openplanner:mayor:gem-a",
        "openplanner:org_admin:gem-x",
        "openplanner:group_admin:gem-a/g9",
        "openplanner:",
        "openplanner:ORG_ADMIN:gem-a",
    )

    assert refused == {
        "openplanner:mayor:gem-a": "unknown_role",
        "openplanner:org_admin:gem-x": "unknown_organisation",
        "openplanner:group_admin:gem-a/g9": "unknown_group",
        "openplanner:": "malformed",
        "openplanner:ORG_ADMIN:gem-a": "malformed",
    }


def test_a_refusal_names_the_organisation_it_can_so_the_right_viewer_sees_it() -> None:
    found = parse_entitlements(
        ["openplanner:group_admin:idp-b/nope", "openplanner:org_admin:gem-x"], PREFIX, DIRECTORY
    )

    assert [(r.value, r.organisation) for r in found.refused] == [
        ("openplanner:group_admin:idp-b/nope", "gem-b"),
        ("openplanner:org_admin:gem-x", None),
    ]


def test_a8_another_prefix_is_ignored() -> None:
    found = parse_entitlements(
        ["openplanner-acc:org_admin:gem-a", "cn=admins,ou=groups", "openplanner:org_admin:gem-a"],
        PREFIX,
        DIRECTORY,
    )

    assert found.grants == {Grant("org_admin", "gem-a")}
    assert found.refused == ()
    assert found.ignored == 2


def test_a7_anything_outside_the_grammar_yields_no_grant() -> None:
    # A property test without a new dependency: generated strings near the grammar.
    rng = random.Random(49)  # noqa: S311 - test data, not security
    alphabet = string.ascii_letters + string.digits + ":/-_*. \t\x00\n"
    valid = {
        "openplanner:superuser",
        "openplanner:org_admin:gem-a",
        "openplanner:auditor:idp-b",
        "openplanner:group_admin:gem-a/g1",
        "openplanner:agent:idp-b/bz",
        "openplanner:custom.3:gem-a/g2",
    }
    tried = {
        "OpenPlanner:ORG_ADMIN:gem-a",
        "openplanner:org_admin:gem-a/*",
        "openplanner:org_admin:gem-a ",
        "openplanner:org_admin:gem-a\x00",
        " openplanner:org_admin:gem-a",
        "openplanner:org_admin:gem-a/g1/g2",
        "openplanner::gem-a",
        "openplanner:org_admin:",
        "openplanner:org_admin:gem-a//",
        "openplanner:org_admin:GEM-A",
    }
    for _ in range(3000):
        base = rng.choice(sorted(valid))
        edited = list(base)
        for _ in range(rng.randint(1, 3)):
            position = rng.randrange(len(edited) + 1)
            action = rng.random()
            if action < 0.4 and edited:
                edited[position % len(edited)] = rng.choice(alphabet)
            elif action < 0.7:
                edited.insert(position, rng.choice(alphabet))
            elif edited:
                del edited[position % len(edited)]
        tried.add("".join(edited))

    for value in tried - valid:
        found = parse_entitlements([value], PREFIX, DIRECTORY)
        assert found.grants == frozenset(), value


def test_entitlement_value_is_the_exact_form_the_idp_must_send() -> None:
    assert entitlement_value(PREFIX, "superuser") == "openplanner:superuser"
    assert entitlement_value(PREFIX, "org_admin", "idp-b") == "openplanner:org_admin:idp-b"
    assert entitlement_value(PREFIX, "agent", "idp-b", "bz") == "openplanner:agent:idp-b/bz"
    assert parse_entitlements(
        [entitlement_value(PREFIX, "agent", "idp-b", "bz")], PREFIX, DIRECTORY
    ).grants == {Grant("agent", "gem-b", "burgerzaken")}


# --- the grant model (points 10 and 11) ----------------------------------------------------


def test_a1_a_role_from_one_organisation_never_reaches_another() -> None:
    mixed = admin(Grant("org_admin", "gem-a"), Grant("auditor", "gem-b"))
    in_b = Target.for_organisation("gem-b")
    group_in_b = Target.for_group("burgerzaken", "gem-b")

    assert not decide(mixed, Permission.BOOKINGS_VIEW, group_in_b).allowed
    assert not decide(mixed, Permission.GROUP_CREATE, in_b).allowed
    assert not decide(mixed, Permission.SETTINGS_ORGANISATION_MANAGE, in_b).allowed
    assert decide(mixed, Permission.AUDIT_VIEW, group_in_b).allowed
    assert decide(mixed, Permission.GROUP_CREATE, Target.for_organisation("gem-a")).allowed
    assert scope_for(mixed, Permission.BOOKINGS_VIEW) == Scope(organisations=frozenset({"gem-a"}))
    assert scope_for(mixed, Permission.AUDIT_VIEW) == Scope(
        organisations=frozenset({"gem-a", "gem-b"})
    )
    for permission in MATRIX[Role.ORG_ADMIN] - MATRIX[Role.AUDITOR]:
        target = {
            "global": Target.everything(),
            "organisation": in_b,
            "group": group_in_b,
        }[LEVELS[permission].value]
        if target.level.value != "global":
            assert not decide(mixed, permission, target).allowed, permission


def test_a2_group_grants_stay_with_their_group() -> None:
    mixed = admin(Grant("group_admin", "gem-a", "g1"), Grant("custom.3", "gem-a", "g2"))
    g1, g2 = Target.for_group("g1", "gem-a"), Target.for_group("g2", "gem-a")

    assert decide(mixed, Permission.SCHEDULE_MANAGE, g1).allowed
    assert not decide(mixed, Permission.SCHEDULE_MANAGE, g2).allowed
    assert decide(mixed, Permission.GROUP_VIEW, g2).allowed
    assert scope_for(mixed, Permission.SCHEDULE_MANAGE) == Scope(
        groups=frozenset({("gem-a", "g1")})
    )
    assert scope_for(mixed, Permission.GROUP_VIEW) == Scope(
        groups=frozenset({("gem-a", "g1"), ("gem-a", "g2")})
    )


def test_a_group_grant_covers_only_the_group_of_its_organisation() -> None:
    # The same group slug in another organisation (A9 at the domain level) never matches.
    one = admin(Grant("group_admin", "gem-a", "g1"))

    assert not decide(one, Permission.GROUP_VIEW, Target.for_group("g1", "gem-b")).allowed
    assert not Scope(groups=frozenset({("gem-a", "g1")})).contains("gem-b", "g1")


def test_decide_never_allows_what_no_single_grant_gives() -> None:
    # ADR 0049: the property of point 10 over every pair of grants and every target.
    rng = random.Random(10)  # noqa: S311 - test data, not security
    places = [
        Grant("org_admin", "gem-a"),
        Grant("auditor", "gem-b"),
        Grant("group_admin", "gem-a", "g1"),
        Grant("agent", "gem-b", "burgerzaken"),
        Grant("custom.3", "gem-a", "g2"),
    ]
    targets = [
        Target.for_organisation("gem-a"),
        Target.for_organisation("gem-b"),
        Target.for_group("g1", "gem-a"),
        Target.for_group("g2", "gem-a"),
        Target.for_group("burgerzaken", "gem-b"),
    ]
    for _ in range(200):
        held = rng.sample(places, rng.randint(1, 3))
        combined = admin(*held)
        for permission in Permission:
            for target in targets:
                if target.level.value != LEVELS[permission].value:
                    continue
                alone = any(decide(admin(g), permission, target).allowed for g in held)
                assert decide(combined, permission, target).allowed == alone, (held, permission)


# --- legacy claims (points 13 and 14) ------------------------------------------------------


def test_legacy_grants_are_the_cross_product_with_known_groups_only() -> None:
    claims = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.ORG_ADMIN, Role.GROUP_ADMIN}),
        organisations=frozenset({"gem-a"}),
        groups=frozenset({"g1", "nope"}),
        role_claims=frozenset({"op-readonly"}),
    )

    assert legacy_grants(claims, DIRECTORY, CATALOGUE) == {
        Grant("org_admin", "gem-a"),
        Grant("group_admin", "gem-a", "g1"),
        Grant("custom.3", "gem-a", "g1"),
    }


def test_a3_ambiguous_legacy_claims_are_refused() -> None:
    ambiguous = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.ORG_ADMIN, Role.AUDITOR}),
        organisations=frozenset({"gem-a", "gem-b"}),
    )
    one_organisation = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.ORG_ADMIN, Role.AUDITOR}),
        organisations=frozenset({"gem-a"}),
    )
    one_role = LoginClaims(
        "s", "S", roles=frozenset({Role.ORG_ADMIN}), organisations=frozenset({"gem-a", "gem-b"})
    )
    custom_counts = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.GROUP_ADMIN}),
        groups=frozenset({"g1", "g2"}),
        role_claims=frozenset({"op-readonly"}),
    )

    assert is_ambiguous(ambiguous, CATALOGUE)
    assert is_ambiguous(custom_counts, CATALOGUE)
    assert not is_ambiguous(one_organisation, CATALOGUE)
    assert not is_ambiguous(one_role, CATALOGUE)
    outcome = resolve(ambiguous, ClaimMode.LEGACY, PREFIX, DIRECTORY, CATALOGUE)
    assert outcome.ambiguous
    assert outcome.principal.grants == frozenset()


def test_a13_shadow_mode_grants_the_legacy_claims_only() -> None:
    claims = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.AUDITOR}),
        organisations=frozenset({"gem-a"}),
        entitlements=frozenset({"openplanner:org_admin:gem-a", "openplanner:org_admin:gem-x"}),
    )

    shadow = resolve(claims, ClaimMode.SHADOW, PREFIX, DIRECTORY, CATALOGUE)
    legacy = resolve(claims, ClaimMode.LEGACY, PREFIX, DIRECTORY, CATALOGUE)
    entitled = resolve(claims, ClaimMode.ENTITLEMENTS, PREFIX, DIRECTORY, CATALOGUE)

    assert shadow.principal.grants == legacy.principal.grants == {Grant("auditor", "gem-a")}
    assert shadow.mismatch == "legacy_only:auditor:gem-a;entitlements_only:org_admin:gem-a"
    assert [r.reason for r in shadow.refused] == [RefusalReason.UNKNOWN_ORGANISATION]
    assert legacy.mismatch is None
    assert legacy.refused == ()
    assert entitled.principal.grants == {Grant("org_admin", "gem-a")}
    assert entitled.mismatch is None


def test_mismatch_detail_counts_when_the_keys_would_not_fit() -> None:
    many = frozenset(Grant("org_admin", f"organisation-number-{n}") for n in range(40))

    assert mismatch_detail(many, frozenset()) == "legacy_only:40;entitlements_only:0"
    assert mismatch_detail(many, many) is None


def test_recognised_values_are_what_the_session_keeps() -> None:
    claims = LoginClaims(
        "s",
        "S",
        entitlements=frozenset(
            {"openplanner:org_admin:gem-a", "openplanner:org_admin:gem-x", "openplanner:x"}
        ),
    )

    assert recognised_values(claims, PREFIX, DIRECTORY) == {"openplanner:org_admin:gem-a"}


def test_resolution_attaches_the_custom_roles_the_grants_name() -> None:
    claims = LoginClaims("s", "S", entitlements=frozenset({"openplanner:custom.3:gem-a/g1"}))

    outcome = resolve(claims, ClaimMode.ENTITLEMENTS, PREFIX, DIRECTORY, CATALOGUE)

    assert outcome.principal.custom_roles == {READ_ONLY}
    assert decide(outcome.principal, Permission.GROUP_VIEW, Target.for_group("g1", "gem-a")).allowed


# --- who sees a refusal (point 8, A14) -----------------------------------------------------


def test_a14_refusals_naming_no_known_organisation_are_for_the_superuser_only() -> None:
    member = StaffMember(
        "s",
        "S",
        frozenset(),
        frozenset(),
        NOW,
        NOW,
        NOW,
        refused=(
            RefusedEntitlement("openplanner:group_admin:gem-a/nope", "unknown_group", "gem-a", NOW),
            RefusedEntitlement("openplanner:org_admin:gem-x", "unknown_organisation", None, NOW),
        ),
    )

    assert [r.value for r in member.within(Scope(organisations=frozenset({"gem-a"}))).refused] == [
        "openplanner:group_admin:gem-a/nope"
    ]
    assert member.within(Scope(organisations=frozenset({"gem-b"}))).refused == ()
    assert len(member.within(Scope(everything=True)).refused) == 2


@pytest.mark.parametrize("reach", list(Reach))
def test_the_directory_knows_every_role_key_with_its_reach(reach: Reach) -> None:
    keys = {key for key, found in DIRECTORY.roles.items() if found is reach}
    built_in = {role.value for role in Role if role.value in keys}

    assert built_in <= keys
    if reach is Reach.GROUPS:
        assert "custom.3" in keys


def test_the_registry_offers_exactly_the_claim_modes() -> None:
    assert set(CLAIM_MODES) == {mode.value for mode in ClaimMode}
