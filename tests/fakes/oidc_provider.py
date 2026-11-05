"""An OIDC provider over HTTP for ``oidc-check``: discovery and signing keys, never a login.

``oidc_provider`` answers on an ``httpx.MockTransport``: the discovery document at ``DISCOVERY``
and the signing keys at ``/jwks``; every other request is a 404. Each request is recorded in the
list the test passes, so a test can show that the check only reads.
"""

import json
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

DISCOVERY = "https://idp.example.nl/.well-known/openid-configuration"


def signing_key(alg: str = "RS256") -> dict[str, Any]:
    """Return the public JWK of a fresh RSA key, key id ``k1``, for signatures with ``alg``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**public, "kid": "k1", "use": "sig", "alg": alg}


def provider_metadata(**extra: Any) -> dict[str, Any]:
    """Return a discovery document the check accepts (RS256, PKCE with S256), with ``extra``."""
    return {
        "issuer": "https://idp.example.nl",
        "authorization_endpoint": "https://idp.example.nl/authorize",
        "token_endpoint": "https://idp.example.nl/token",
        "jwks_uri": "https://idp.example.nl/jwks",
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        **extra,
    }


def oidc_provider(
    seen: list[httpx.Request],
    metadata: dict[str, Any] | None = None,
    keys: list[dict[str, Any]] | None = None,
    discovery_status: int = 200,
) -> httpx.MockTransport:
    """Return the transport of a provider that serves ``metadata`` and ``keys``.

    The discovery answer has ``discovery_status``; without ``metadata`` it is
    ``provider_metadata()``, without ``keys`` the key set holds one ``signing_key()``.
    """

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == DISCOVERY:
            return httpx.Response(discovery_status, json=metadata or provider_metadata())
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [signing_key()] if keys is None else keys})
        return httpx.Response(404)

    return httpx.MockTransport(route)
