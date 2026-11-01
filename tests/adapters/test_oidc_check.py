"""``oidc-check``: the login configuration checked against the provider, without a login."""

import json
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.diagnostics import passed
from app.adapters.oidc_check import OidcTarget, check_oidc

DISCOVERY = "https://idp.example.nl/.well-known/openid-configuration"
TARGET = OidcTarget(
    discovery_url=DISCOVERY,
    redirect_uri="https://afspraak.example.nl/admin/callback",
    require_https=True,
    client_secret_set=True,
)


def _jwk(alg: str = "RS256") -> dict[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**public, "kid": "k1", "use": "sig", "alg": alg}


def _metadata(**extra: Any) -> dict[str, Any]:
    return {
        "issuer": "https://idp.example.nl",
        "authorization_endpoint": "https://idp.example.nl/authorize",
        "token_endpoint": "https://idp.example.nl/token",
        "jwks_uri": "https://idp.example.nl/jwks",
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        **extra,
    }


def _transport(
    seen: list[httpx.Request],
    metadata: dict[str, Any] | None = None,
    keys: list[dict[str, Any]] | None = None,
    discovery_status: int = 200,
) -> httpx.MockTransport:
    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == DISCOVERY:
            return httpx.Response(discovery_status, json=metadata or _metadata())
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [_jwk()] if keys is None else keys})
        return httpx.Response(404)

    return httpx.MockTransport(route)


async def _check(transport: httpx.MockTransport, target: OidcTarget = TARGET) -> list[Any]:
    async with httpx.AsyncClient(transport=transport) as http:
        return await check_oidc(http, target)


async def test_a_sound_configuration_passes_with_two_reads_and_no_login() -> None:
    seen: list[httpx.Request] = []

    results = await _check(_transport(seen))

    assert passed(results), results
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/.well-known/openid-configuration"),
        ("GET", "/jwks"),
    ]
    assert {r.name for r in results} >= {
        "discovery",
        "signing algorithms",
        "signing keys",
        "PKCE S256",
        "redirect URL",
        "client secret",
    }


async def test_an_unreachable_discovery_fails_and_stops() -> None:
    seen: list[httpx.Request] = []

    results = await _check(_transport(seen, discovery_status=503))

    assert not passed(results)
    assert results[0].name == "discovery"
    assert results[0].status == 503
    assert len(seen) == 1


async def test_a_provider_that_signs_only_symmetrically_fails() -> None:
    metadata = _metadata(id_token_signing_alg_values_supported=["HS256", "none"])

    results = await _check(_transport([], metadata))

    (algorithms,) = [r for r in results if r.name == "signing algorithms"]
    assert not algorithms.ok
    assert "HS256" in algorithms.hint


@pytest.mark.parametrize(
    "keys",
    [[], [{"kty": "oct", "k": "c2VjcmV0", "kid": "k1"}], [{"kty": "RSA", "kid": "broken"}]],
)
async def test_a_key_set_without_a_usable_asymmetric_key_fails(keys: list[dict[str, Any]]) -> None:
    results = await _check(_transport([], keys=keys))

    (signing_keys,) = [r for r in results if r.name == "signing keys"]
    assert not signing_keys.ok
    assert "c2VjcmV0" not in signing_keys.hint


async def test_an_http_redirect_fails_in_production_but_not_in_development() -> None:
    http_target = OidcTarget(
        discovery_url=DISCOVERY,
        redirect_uri="http://127.0.0.1:8000/admin/callback",
        require_https=True,
        client_secret_set=True,
    )
    results = await _check(_transport([]), http_target)
    (redirect,) = [r for r in results if r.name == "redirect URL"]
    assert not redirect.ok
    assert "http://127.0.0.1:8000/admin/callback" in redirect.hint

    dev = OidcTarget(
        DISCOVERY, http_target.redirect_uri, require_https=False, client_secret_set=False
    )
    assert passed(await _check(_transport([]), dev))


@pytest.mark.parametrize(
    ("expected", "ok"),
    [
        (None, True),
        ("https://idp.example.nl", True),
        ("https://idp.example.nl/", False),
        ("https://other.example.nl", False),
    ],
)
async def test_the_issuer_is_compared_exactly_with_the_one_the_login_pins(
    expected: str | None, ok: bool
) -> None:
    target = OidcTarget(
        DISCOVERY,
        TARGET.redirect_uri,
        require_https=True,
        client_secret_set=True,
        expected_issuer=expected,
    )

    results = await _check(_transport([]), target)

    (discovery,) = [r for r in results if r.name == "discovery"]
    assert discovery.ok is ok
    assert passed(results) is ok
    if not ok:
        assert "OIDC_ISSUER" in discovery.hint


async def test_an_issuer_announced_elsewhere_than_the_discovery_url_names_fails_without_pin() -> (
    None
):
    metadata = _metadata(issuer="https://login.example.nl/realms/openplanner")

    results = await _check(_transport([], metadata))

    (discovery,) = [r for r in results if r.name == "discovery"]
    assert not discovery.ok
    assert "https://login.example.nl/realms/openplanner" in discovery.hint


async def test_plain_http_back_channel_endpoints_fail_in_production_only() -> None:
    metadata = _metadata(
        token_endpoint="http://idp.example.nl/token", jwks_uri="http://idp.example.nl/jwks"
    )

    results = await _check(_transport([], metadata))

    (endpoints,) = [r for r in results if r.name == "provider endpoints"]
    assert not endpoints.ok
    assert "token_endpoint" in endpoints.hint
    assert "jwks_uri" in endpoints.hint
    dev = OidcTarget(DISCOVERY, TARGET.redirect_uri, require_https=False, client_secret_set=True)
    (dev_endpoints,) = [
        r for r in await _check(_transport([], metadata), dev) if r.name == "provider endpoints"
    ]
    assert dev_endpoints.ok


async def test_a_missing_client_secret_fails_in_production() -> None:
    target = OidcTarget(DISCOVERY, TARGET.redirect_uri, require_https=True, client_secret_set=False)

    results = await _check(_transport([]), target)

    (secret,) = [r for r in results if r.name == "client secret"]
    assert not secret.ok
    assert secret.hint == "not set"


async def test_a_provider_without_s256_only_warns() -> None:
    metadata = _metadata(code_challenge_methods_supported=["plain"])

    results = await _check(_transport([], metadata))

    (pkce,) = [r for r in results if r.name == "PKCE S256"]
    assert not pkce.ok
    assert not pkce.required
    assert passed(results)
