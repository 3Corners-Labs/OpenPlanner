"""The rules of managing staff in the bundled identity provider (ADR 0095)."""

from app.domain.authorization import REACH, Grant, Principal, Scope
from app.domain.entitlements import Directory, GroupRef, OrganisationRef
from app.domain.staff_directory import DirectoryRefusal, plan_entitlements, refusal_of

DIRECTORY = Directory(
    {role.value: reach for role, reach in REACH.items()},
    (OrganisationRef("demo", "demo-idp"), OrganisationRef("andere", "andere-idp")),
    (GroupRef("team", "team-idp", "demo"), GroupRef("elders", "elders-idp", "andere")),
)
PREFIX = "openplanner"

SUPERUSER = Principal("s", "S", frozenset({Grant("superuser")}))
ORG_ADMIN = Principal("o", "O", frozenset({Grant("org_admin", "demo")}))
ORG_SCOPE = Scope(organisations=frozenset({"demo"}))
EVERYTHING = Scope(everything=True)


def test_an_organisation_admin_may_grant_a_role_in_the_own_organisation() -> None:
    assert refusal_of(ORG_SCOPE, Grant("org_admin", "demo"), superuser=False) is None
    assert refusal_of(ORG_SCOPE, Grant("agent", "demo", "team"), superuser=False) is None


def test_an_organisation_admin_may_not_grant_in_another_organisation() -> None:
    assert (
        refusal_of(ORG_SCOPE, Grant("org_admin", "andere"), superuser=False)
        is DirectoryRefusal.OUT_OF_SCOPE
    )
    assert (
        refusal_of(ORG_SCOPE, Grant("agent", "andere", "elders"), superuser=False)
        is DirectoryRefusal.OUT_OF_SCOPE
    )


def test_only_the_superuser_may_grant_the_superuser_role() -> None:
    assert (
        refusal_of(ORG_SCOPE, Grant("superuser"), superuser=False)
        is DirectoryRefusal.ABOVE_OWN_RIGHTS
    )
    assert refusal_of(EVERYTHING, Grant("superuser"), superuser=True) is None


def test_a_custom_role_is_never_written_here() -> None:
    assert (
        refusal_of(EVERYTHING, Grant("custom.3", "demo"), superuser=True)
        is DirectoryRefusal.UNSUPPORTED_ROLE
    )


def test_the_reach_of_the_role_decides_the_shape_of_the_grant() -> None:
    for grant in (
        Grant("org_admin", "demo", "team"),
        Grant("agent", "demo"),
        Grant("superuser", "demo"),
    ):
        assert refusal_of(EVERYTHING, grant, superuser=True) is DirectoryRefusal.SHAPE


def test_the_plan_replaces_only_values_inside_the_actors_scope() -> None:
    existing = [
        f"{PREFIX}:org_admin:demo-idp",
        f"{PREFIX}:org_admin:andere-idp",
        "unrelated:value",
    ]

    plan = plan_entitlements(existing, {Grant("auditor", "demo")}, ORG_SCOPE, PREFIX, DIRECTORY)

    assert sorted(plan) == sorted(
        ["unrelated:value", f"{PREFIX}:org_admin:andere-idp", f"{PREFIX}:auditor:demo-idp"]
    )


def test_the_plan_keeps_values_it_cannot_read() -> None:
    existing = [f"{PREFIX}:nonsense", f"{PREFIX}:org_admin:ghost"]

    plan = plan_entitlements(existing, set(), ORG_SCOPE, PREFIX, DIRECTORY)

    assert sorted(plan) == sorted(existing)


def test_an_empty_wish_removes_what_the_actor_may_change_and_keeps_the_rest() -> None:
    existing = [f"{PREFIX}:agent:demo-idp/team-idp", f"{PREFIX}:superuser"]

    plan = plan_entitlements(existing, set(), ORG_SCOPE, PREFIX, DIRECTORY)

    assert plan == [f"{PREFIX}:superuser"]


def test_the_superuser_scope_replaces_every_value_it_can_read() -> None:
    existing = [f"{PREFIX}:org_admin:andere-idp", f"{PREFIX}:superuser"]

    plan = plan_entitlements(existing, {Grant("org_admin", "demo")}, EVERYTHING, PREFIX, DIRECTORY)

    assert plan == [f"{PREFIX}:org_admin:demo-idp"]
