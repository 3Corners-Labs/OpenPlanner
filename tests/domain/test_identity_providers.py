"""The registration model and the discovery check of ADR 0083 (points 5 to 7)."""

from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import (
    KEY_OVERLAP,
    ClientAuth,
    DiscoveryRefusedError,
    ProviderDraft,
    SigningKey,
    check_discovery,
    is_discovery_url,
    issuer_of,
    jwks,
    validate_scopes,
    validate_secret,
)
from tests.fakes.identity_providers import DISCOVERY_URL, ISSUER, discovery_document
from tests.support.domain import NOW


def draft(**overrides: str) -> ProviderDraft:
    values = {
        "key": "gem-a",
        "name": "Gemeente A",
        "discovery_url": DISCOVERY_URL,
        "client_id": "openplanner",
        "client_auth": "private_key_jwt",
        "scopes": "openid profile email",
        "entitlement_claim": "openplanner_entitlements",
        "entitlement_prefix": "openplanner",
    }
    values.update(overrides)
    return ProviderDraft.create(**values)


def test_a_draft_is_normalised() -> None:
    made = draft(
        name="  Gemeente A ", scopes="openid  email openid", discovery_url=f" {DISCOVERY_URL} "
    )

    assert made.name == "Gemeente A"
    assert made.scopes == "openid email"
    assert made.discovery_url == DISCOVERY_URL
    assert made.client_auth is ClientAuth.PRIVATE_KEY_JWT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", "Gem A"),
        ("key", ""),
        ("name", ""),
        ("name", "x" * 81),
        ("name", "a\x00b"),
        ("discovery_url", "http://login.gem-a.example/.well-known/openid-configuration"),
        ("discovery_url", "https://login.gem-a.example/"),
        ("discovery_url", "https://login.gem-a.example/.well-known/openid-configuration?x=1"),
        ("discovery_url", "https://user:pw@login.gem-a.example/.well-known/openid-configuration"),
        ("client_id", ""),
        ("client_auth", "none"),
        ("scopes", "profile email"),
        ("scopes", 'openid "quoted"'),
        ("entitlement_claim", "1claim"),
        ("entitlement_claim", "a b"),
        ("entitlement_prefix", "Openplanner"),
        ("entitlement_prefix", "a"),
    ],
)
def test_an_invalid_field_is_refused_by_name_never_by_value(field: str, value: str) -> None:
    with pytest.raises(DomainError) as caught:
        draft(**{field: value})

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.field == field
    assert str(caught.value) == f"invalid_configuration: {field}"


def test_the_discovery_url_names_the_issuer() -> None:
    assert is_discovery_url(DISCOVERY_URL)
    assert issuer_of(DISCOVERY_URL) == ISSUER
    assert not is_discovery_url("https://.well-known/openid-configuration")


def test_scopes_need_openid_and_a_secret_is_bounded() -> None:
    assert validate_scopes("openid") == "openid"
    with pytest.raises(DomainError):
        validate_scopes("")
    validate_secret("s3cret")
    for bad in ("", "x" * 513, "a\nb"):
        with pytest.raises(DomainError) as caught:
            validate_secret(bad)
        assert caught.value.field == "client_secret"


def test_a_sound_discovery_document_returns_its_issuer() -> None:
    assert (
        check_discovery(discovery_document(), DISCOVERY_URL, ClientAuth.PRIVATE_KEY_JWT) == ISSUER
    )


def test_a_trailing_slash_on_the_issuer_is_a_mismatch() -> None:
    with pytest.raises(DiscoveryRefusedError) as caught:
        check_discovery(
            discovery_document(issuer=f"{ISSUER}/"), DISCOVERY_URL, ClientAuth.PRIVATE_KEY_JWT
        )
    assert caught.value.reason == "issuer_mismatch"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"issuer": None}, "missing_issuer"),
        ({"issuer": 7}, "missing_issuer"),
        ({"jwks_uri": None}, "missing_jwks_uri"),
        ({"token_endpoint": ""}, "missing_token_endpoint"),
        ({"issuer": "https://evil.example"}, "issuer_mismatch"),
        ({"response_types_supported": ["id_token"]}, "no_code_flow"),
        ({"response_types_supported": None}, "no_code_flow"),
        ({"code_challenge_methods_supported": ["plain"]}, "no_pkce"),
        ({"id_token_signing_alg_values_supported": ["HS256", "none"]}, "no_asymmetric_signature"),
        (
            {"token_endpoint_auth_methods_supported": ["client_secret_post"]},
            "auth_method_unsupported",
        ),
        ({"token_endpoint_auth_methods_supported": None}, "auth_method_unsupported"),
    ],
)
def test_a_document_that_fails_a_rule_of_point_7_is_refused(
    overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(DiscoveryRefusedError) as refused:
        check_discovery(discovery_document(**overrides), DISCOVERY_URL, ClientAuth.PRIVATE_KEY_JWT)

    assert refused.value.reason == reason


def test_client_secret_basic_is_the_default_method_when_none_is_announced() -> None:
    document = discovery_document(token_endpoint_auth_methods_supported=None)

    assert check_discovery(document, DISCOVERY_URL, ClientAuth.CLIENT_SECRET_BASIC) == ISSUER


def test_a_retired_key_is_published_for_the_overlap_only() -> None:
    key = SigningKey("gem-a", "kid-1", {"kty": "EC"}, NOW, retired_at=NOW)

    assert key.published(NOW + KEY_OVERLAP - timedelta(seconds=1))
    assert not key.published(NOW + KEY_OVERLAP)
    assert SigningKey("gem-a", "kid-2", {"kty": "EC"}, NOW).published(NOW + timedelta(days=365))


def test_the_jwks_is_stable_and_carries_the_key_ids() -> None:
    keys = [
        SigningKey("gem-b", "kid-b", {"kty": "EC", "x": "b"}, NOW),
        SigningKey("gem-a", "kid-a", {"kty": "EC", "x": "a"}, NOW),
    ]

    assert jwks(keys) == {
        "keys": [{"kty": "EC", "x": "a", "kid": "kid-a"}, {"kty": "EC", "x": "b", "kid": "kid-b"}]
    }
