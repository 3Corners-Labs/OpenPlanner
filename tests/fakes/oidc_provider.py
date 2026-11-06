"""An OIDC provider over HTTP for ``oidc-check``: discovery and signing keys, never a login.

``oidc_provider`` answers on an ``httpx.MockTransport``: the discovery document at ``DISCOVERY``
and the signing keys at ``/jwks``; every other request is a 404. Each request is recorded in the
list the test passes, so a test can show that the check only reads. The document is the one
``discovery_document`` builds for every fake provider, here for ``ISSUER``.
"""

import json
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.fakes.identity_providers import discovery_document

ISSUER = "https://idp.example.nl"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"


def signing_key(alg: str = "RS256") -> dict[str, Any]:
    """Return the public JWK of a fresh RSA key, key id ``k1``, for signatures with ``alg``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**public, "kid": "k1", "use": "sig", "alg": alg}


def oidc_provider(
    seen: list[httpx.Request],
    metadata: dict[str, Any] | None = None,
    keys: list[dict[str, Any]] | None = None,
    discovery_status: int = 200,
) -> httpx.MockTransport:
    """Return the transport of a provider that serves ``metadata`` and ``keys``.

    The discovery answer has ``discovery_status``; without ``metadata`` it is
    ``discovery_document(ISSUER)``, without ``keys`` the key set holds one ``signing_key()``.
    """

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == DISCOVERY:
            return httpx.Response(discovery_status, json=metadata or discovery_document(ISSUER))
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [signing_key()] if keys is None else keys})
        return httpx.Response(404)

    return httpx.MockTransport(route)
