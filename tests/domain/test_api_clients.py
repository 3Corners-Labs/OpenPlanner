"""API client credentials and input validation (ADR 0023)."""

from datetime import timedelta

import pytest

from app.domain.api_clients import (
    CLIENT_ID_PREFIX,
    MAX_NETWORKS,
    SECRET_PREFIX,
    TOKEN_PREFIX,
    ApiAuthError,
    ApiClient,
    ClientDraft,
    address_allowed,
    credential_hash,
    credential_matches,
    is_client_id,
    new_access_token,
    new_client_id,
    new_secret,
    parse_networks,
)
from app.domain.authorization import ApiScope
from app.domain.errors import DomainError, ErrorCode
from tests.support.domain import NOW


def test_credentials_are_random_prefixed_and_only_their_hash_is_compared() -> None:
    secret, token = new_secret(), new_access_token()

    assert secret.startswith(SECRET_PREFIX)
    assert token.startswith(TOKEN_PREFIX)
    assert min(len(secret), len(token)) > 40
    assert new_secret() != secret
    assert credential_matches(secret, credential_hash(secret))
    assert not credential_matches(secret, credential_hash(token))
    assert secret not in credential_hash(secret)


def test_client_ids_are_recognised_by_shape() -> None:
    client_id = new_client_id()

    assert client_id.startswith(CLIENT_ID_PREFIX)
    assert is_client_id(client_id)
    for bad in ("", "opc_short", "x" + client_id, client_id + "\n", "opc_" + "a" * 65):
        assert not is_client_id(bad)


def test_networks_are_normalised_and_deduplicated() -> None:
    lines = ["  10.0.0.7/24", "", "10.0.0.0/24", "2001:db8::1", "192.0.2.1"]

    assert parse_networks(lines) == ("10.0.0.0/24", "2001:db8::1/128", "192.0.2.1/32")


TOO_MANY = [f"10.0.{i}.0/24" for i in range(MAX_NETWORKS + 1)]


@pytest.mark.parametrize("lines", [["not-a-network"], ["10.0.0.0/33"], TOO_MANY])
def test_bad_or_too_many_networks_are_refused(lines: list[str]) -> None:
    with pytest.raises(DomainError) as caught:
        parse_networks(lines)
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, "networks")


def test_address_allow_list() -> None:
    networks = ("10.0.0.0/24", "2001:db8::/32")

    assert address_allowed((), None)
    assert address_allowed(networks, "10.0.0.9")
    assert address_allowed(networks, "2001:db8::5")
    assert not address_allowed(networks, "10.0.1.9")
    assert not address_allowed(networks, None)
    assert not address_allowed(networks, "testclient")


def test_draft_validates_name_and_scopes() -> None:
    draft = ClientDraft.create(
        name="  WxCC   AI Agent ",
        organisation="demo",
        groups=["demo", ""],
        scopes=["availability:read"],
        networks=[],
    )

    assert draft.name == "WxCC AI Agent"
    assert draft.groups == {"demo"}
    assert draft.scopes == {ApiScope.AVAILABILITY_READ}


@pytest.mark.parametrize(
    ("name", "scopes", "field"),
    [
        ("", ["availability:read"], "name"),
        ("x" * 81, ["availability:read"], "name"),
        ("bot\x00", ["availability:read"], "name"),
        ("bot", [], "scopes"),
        ("bot", ["bookings:everything"], "scopes"),
    ],
)
def test_draft_refuses_bad_input(name: str, scopes: list[str], field: str) -> None:
    with pytest.raises(DomainError) as caught:
        ClientDraft.create(name=name, organisation="demo", groups=[], scopes=scopes, networks=[])
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)


def test_secret_expiry_warning_and_repr_without_hash() -> None:
    client = ApiClient(
        None,
        new_client_id(),
        "bot",
        "demo",
        frozenset(),
        frozenset({ApiScope.AVAILABILITY_READ}),
        (),
        True,
        "hash-value",
        NOW + timedelta(days=31),
        NOW,
    )

    assert not client.secret_expiring(NOW)
    assert client.secret_expiring(NOW + timedelta(days=2))
    assert "hash-value" not in repr(client)


def test_auth_error_message_is_the_code_only() -> None:
    error = ApiAuthError("invalid_client", "bad_secret")

    assert str(error) == "invalid_client"
    assert error.reason == "bad_secret"
