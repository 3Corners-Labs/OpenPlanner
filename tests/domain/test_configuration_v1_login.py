"""Identity providers, signing keys and login domains in a document (ADR 0083, 0099)."""

from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction, RuleContext
from app.domain.configuration_document import to_json
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_login import (
    DOMAIN,
    IDENTITY_PROVIDERS,
    INTEGRITY,
    PROVIDER,
    SIGNING_KEY,
)
from app.domain.configuration_values import Secret
from app.domain.errors import ErrorCode
from tests.support.configuration import Recorder, entity, parse, refusals
from tests.support.configuration_columns import uncovered_columns
from tests.support.configuration_parts import installation, organisation, plan_opened, stub_format

FMT = stub_format((IDENTITY_PROVIDERS,), (), *INTEGRITY)
DISCOVERY = "https://idp.acme.nl/.well-known/openid-configuration"
SECRET = (
    "aHVudGVyMi1zZWNyZXQ="  # gitleaks:allow (test value: base64 the test opens as the plain secret)
)


def provider(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "key": "acme-sso",
        "name": "Acme SSO",
        "discovery_url": DISCOVERY,
        "client_id": "openplanner",
        "client_auth": "private_key_jwt",
        "entitlement_claim": "groups",
        "entitlement_prefix": "op",
    }
    values.update(overrides)
    return values


def doc(*providers: dict[str, Any], org: str = "acme") -> dict[str, Any]:
    return {"organisations": [{"slug": org, "identity_providers": list(providers)}]}


def current_provider(key: str = "acme-sso", **overrides: Any) -> Entity:
    fields = {k: v for k, v in provider(key=key).items() if k != "key"}
    fields |= {"scopes": "openid profile email", "enabled": False, "mfa_policy": "record"}
    fields |= {"mfa_amr_values": "", "mfa_acr_values": ""}
    fields |= overrides
    return entity("identity_provider", (key,), fields)


def current(*providers: Entity, org: str = "acme") -> Entity:
    return installation(organisation(org, identity_providers=providers))


def test_a_provider_is_created_with_the_defaults_of_the_portal_form() -> None:
    result = plan_opened(doc(provider()), current(), FMT)

    (change,) = result.changes
    assert result.ok
    assert (change.kind, change.address, change.action) == (
        "identity_provider",
        ("acme", "acme-sso"),
        ChangeAction.CREATE,
    )
    assert change.values["scopes"] == "openid profile email"
    assert change.values["enabled"] is False
    assert change.values["mfa_policy"] == "record"
    assert change.permissions == {Permission.IDENTITY_PROVIDERS_MANAGE}


def test_a_provider_is_checked_against_the_global_permission_of_its_screen() -> None:
    recorder = Recorder()

    plan_opened(doc(provider()), current(), FMT, may_change=recorder)

    assert recorder.asked == [(Permission.IDENTITY_PROVIDERS_MANAGE, Target.everything())]


def test_the_fields_are_normalised_as_the_portal_stores_them() -> None:
    data = doc(provider(scopes="openid   email  openid", name="  Acme SSO "))

    (change,) = plan_opened(data, current(), FMT).changes

    assert change.values["scopes"] == "openid email"
    assert change.values["name"] == "Acme SSO"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"discovery_url": "https://idp.acme.nl/other"}, "discovery_url"),
        ({"discovery_url": "http://idp.acme.nl/.well-known/openid-configuration"}, "discovery_url"),
        ({"scopes": "profile"}, "scopes"),
        ({"entitlement_claim": "9bad"}, "entitlement_claim"),
        ({"entitlement_prefix": "X"}, "entitlement_prefix"),
        ({"mfa_amr_values": "bad value!"}, "mfa_amr_values"),
        ({"key": "Not A Key"}, "key"),
    ],
)
def test_a_provider_the_screen_would_refuse_is_refused_by_field(
    overrides: dict[str, Any], field: str
) -> None:
    context = RuleContext(production=True)

    result = plan_opened(doc(provider(**overrides)), current(), FMT, context=context)

    assert refusals(result) == [
        (
            "organisations[acme].identity_providers[" + provider(**overrides)["key"] + "]",
            ErrorCode.INVALID_CONFIGURATION,
            field,
        )
    ]


def test_an_http_discovery_address_is_only_for_a_lab_installation() -> None:
    data = doc(provider(discovery_url="http://idp.lab/.well-known/openid-configuration"))

    assert plan_opened(data, current(), FMT).ok
    assert not plan_opened(data, current(), FMT, context=RuleContext(production=True)).ok


def test_a_client_secret_provider_needs_a_secret_and_a_key_jwt_provider_refuses_one() -> None:
    basic = provider(client_auth="client_secret_basic")
    path = "organisations[acme].identity_providers[acme-sso]"

    missing = plan_opened(doc(basic), current(), FMT)
    given = plan_opened(doc({**basic, "client_secret": {"sealed": SECRET}}), current(), FMT)
    superfluous = plan_opened(
        doc({**provider(), "client_secret": {"sealed": SECRET}}), current(), FMT
    )

    assert refusals(missing) == [(path, ErrorCode.INVALID_CONFIGURATION, "client_secret")]
    assert given.ok
    assert refusals(superfluous) == [(path, ErrorCode.INVALID_CONFIGURATION, "client_secret")]


def test_a_stored_client_secret_is_kept_and_a_bad_new_one_is_refused() -> None:
    stored = current_provider(client_auth="client_secret_basic", client_secret=Secret("old-secret"))
    keep = {"key": "acme-sso", "name": "Renamed"}
    path = "organisations[acme].identity_providers[acme-sso]"

    kept = plan_opened(doc(keep), current(stored), FMT)
    too_long = plan_opened(
        doc({**keep, "client_secret": {"sealed": "QUFB" * 150}}), current(stored), FMT
    )

    assert kept.ok
    assert kept.changes[0].fields == ("name",)
    assert refusals(too_long) == [(path, ErrorCode.INVALID_CONFIGURATION, "client_secret")]


def test_changing_only_the_switch_names_only_the_switch() -> None:
    result = plan_opened(
        doc({"key": "acme-sso", "enabled": True}), current(current_provider()), FMT
    )

    (change,) = result.changes
    assert (change.action, change.fields) == (ChangeAction.UPDATE, ("enabled",))


def test_a_provider_is_deleted_and_nothing_is_left_to_refuse() -> None:
    result = plan_opened(
        doc({"key": "acme-sso", "action": "delete"}), current(current_provider()), FMT
    )

    assert [(c.action, c.permissions) for c in result.changes] == [
        (ChangeAction.DELETE, {Permission.IDENTITY_PROVIDERS_MANAGE})
    ]


def signing_key(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "kid": "k1",
        "public_jwk": {"kty": "EC", "crv": "P-256", "x": "a", "y": "b"},
        "private_key": {"sealed": SECRET},
        "created_at": "2026-11-01T10:00:00+01:00",
    }
    values.update(overrides)
    return values


def test_a_signing_key_needs_its_private_half_and_times_with_a_zone() -> None:
    path = "organisations[acme].identity_providers[acme-sso].keys[k1]"
    base = provider()

    ok = plan_opened(doc({**base, "keys": [signing_key()]}), current(), FMT)
    no_private = plan_opened(
        doc({**base, "keys": [{k: v for k, v in signing_key().items() if k != "private_key"}]}),
        current(),
        FMT,
    )
    naive = plan_opened(
        doc({**base, "keys": [signing_key(created_at="2026-11-01T10:00")]}), current(), FMT
    )
    retired = plan_opened(doc({**base, "keys": [signing_key(retired_at="soon")]}), current(), FMT)
    bad_jwk = plan_opened(
        doc({**base, "keys": [signing_key(public_jwk={"kty": 5})]}), current(), FMT
    )

    key_change = ok.changes[1]
    assert (key_change.kind, key_change.values["created_at"]) == (
        "identity_provider_key",
        "2026-11-01T09:00:00+00:00",
    )
    assert refusals(no_private) == [(path, ErrorCode.INVALID_CONFIGURATION, "private_key")]
    assert refusals(naive) == [(path, ErrorCode.INVALID_CONFIGURATION, "created_at")]
    assert refusals(retired) == [(path, ErrorCode.INVALID_CONFIGURATION, "retired_at")]
    assert refusals(bad_jwk) == [(path, ErrorCode.INVALID_CONFIGURATION, "public_jwk")]


def test_a_key_can_be_retired_without_giving_its_private_half_again() -> None:
    stored = entity(
        "identity_provider_key",
        ("k1",),
        {
            "public_jwk": {"kty": "EC"},
            "private_key": Secret("private"),
            "created_at": "2026-11-01T09:00:00+00:00",
            "retired_at": None,
        },
    )
    parent = current_provider()
    parent = Entity(parent.kind, parent.key, fields=parent.fields, children={"keys": (stored,)})
    data = doc({"key": "acme-sso", "keys": [{"kid": "k1", "retired_at": "2026-11-05T00:00:00Z"}]})

    (change,) = plan_opened(data, current(parent), FMT).changes

    assert (change.kind, change.fields) == ("identity_provider_key", ("retired_at",))
    assert change.values["retired_at"] == "2026-11-05T00:00:00+00:00"


def test_a_domain_is_a_plain_key_that_must_be_written_normalised() -> None:
    path = "organisations[acme].identity_providers[acme-sso].domains[Acme.NL]"

    ok = plan_opened(doc({**provider(), "domains": ["acme.nl"]}), current(), FMT)
    upper = plan_opened(doc({**provider(), "domains": ["Acme.NL"]}), current(), FMT)
    bare = plan_opened(doc({**provider(), "domains": ["acme"]}), current(), FMT)

    assert [c.kind for c in ok.changes] == ["identity_provider", "identity_provider_domain"]
    assert refusals(upper) == [(path, ErrorCode.IDP_DOMAIN_INVALID, "domain")]
    assert refusals(bare)[0][1] is ErrorCode.IDP_DOMAIN_INVALID


def test_a_new_domain_under_the_reserved_suffix_is_refused() -> None:
    context = RuleContext(settings={"bundled_idp_login_suffix": "openplanner.internal"})
    path = "organisations[acme].identity_providers[acme-sso].domains[sub.openplanner.internal]"

    result = plan_opened(
        doc({**provider(), "domains": ["sub.openplanner.internal"]}),
        current(),
        FMT,
        context=context,
    )

    assert refusals(result) == [(path, ErrorCode.IDP_DOMAIN_RESERVED, "domain")]


def test_a_domain_another_provider_claims_is_taken_and_one_that_was_there_is_left_alone() -> None:
    other = entity(
        "identity_provider",
        ("other",),
        {"client_auth": "private_key_jwt"},
        {"domains": (entity("identity_provider_domain", ("acme.nl",)),)},
    )
    tree = current(other)
    path = "organisations[acme].identity_providers[acme-sso].domains[acme.nl]"

    taken = plan_opened(doc({**provider(), "domains": ["acme.nl"]}), tree, FMT)
    same = plan_opened(doc({"key": "other", "domains": ["acme.nl"]}), tree, FMT)

    assert refusals(taken) == [(path, ErrorCode.IDP_DOMAIN_TAKEN, "domain")]
    assert same.ok
    assert same.changes == ()


def test_a_provider_cannot_claim_more_than_twenty_domains() -> None:
    names = [f"d{n}.acme.nl" for n in range(21)]

    result = plan_opened(doc({**provider(), "domains": names}), current(), FMT)

    assert [r.code for r in result.refusals] == [ErrorCode.IDP_DOMAIN_LIMIT]
    assert result.refusals[0].path.endswith("domains[d20.acme.nl]")


def test_a_provider_key_belongs_to_one_organisation_only() -> None:
    tree = installation(
        organisation("acme"),
        organisation("beta", identity_providers=(current_provider("acme-sso"),)),
    )
    data = {"organisations": [{"slug": "acme", "identity_providers": [provider()]}]}

    result = plan_opened(data, tree, FMT)

    assert refusals(result) == [
        ("organisations[acme].identity_providers[acme-sso]", ErrorCode.IDP_KEY_TAKEN, "key")
    ]


def test_an_organisation_has_at_most_the_configured_number_of_providers() -> None:
    tree = current(current_provider("one"), current_provider("two"))
    three = doc(provider(key="three"))

    default = plan_opened(three, tree, FMT)
    raised = plan_opened(
        three, tree, FMT, context=RuleContext(settings={"idp_max_per_organisation": 3})
    )

    assert refusals(default) == [
        (
            "organisations[acme].identity_providers[three]",
            ErrorCode.IDENTITY_PROVIDER_LIMIT_REACHED,
            None,
        )
    ]
    assert raised.ok


def test_a_provider_over_the_limit_that_already_exists_is_not_refused() -> None:
    tree = current(current_provider("one"), current_provider("two"), current_provider("three"))

    result = plan_opened(
        doc({"key": "three", "enabled": True}),
        tree,
        FMT,
        context=RuleContext(settings={"idp_max_per_organisation": 2}),
    )

    assert result.ok


def test_a_document_with_keys_and_domains_reads_and_writes_the_same() -> None:
    data = doc(
        {
            **provider(client_auth="client_secret_basic"),
            "client_secret": {"sealed": SECRET},
            "keys": [{**signing_key(), "retired_at": None}],
            "domains": ["acme.nl", "acme.example"],
        }
    )

    assert to_json(parse(data, FMT)) == {"format": "test/v1", **data}


def test_the_kinds_cover_every_column_of_their_tables() -> None:
    assert uncovered_columns([PROVIDER, SIGNING_KEY, DOMAIN]) == {}
