"""Fakes for the organisation identity providers (ADR 0083): discovery and key generation."""

from collections.abc import Mapping
from typing import Any

from app.domain.identity_providers import GeneratedKey
from app.domain.ports import IdentityError

ISSUER = "https://login.gem-a.example"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"


def discovery_document(issuer: str = ISSUER, **overrides: Any) -> dict[str, Any]:
    """Return a discovery document that passes the check of ADR 0083 point 7."""
    document: dict[str, Any] = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256", "ES256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "private_key_jwt"],
    }
    document.update(overrides)
    return {k: v for k, v in document.items() if v is not None}


class FakeDiscoveryFetcher:
    """``DiscoveryFetcher`` fake: a document per URL, or a failure code."""

    def __init__(self) -> None:
        self.documents: dict[str, Mapping[str, object]] = {DISCOVERY_URL: discovery_document()}
        self.fail: str | None = None
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> Mapping[str, object]:
        self.fetched.append(url)
        if self.fail:
            raise IdentityError(self.fail)
        if url not in self.documents:
            raise IdentityError("discovery_failed")
        return self.documents[url]


class FakeSigningKeys:
    """``SigningKeys`` fake: numbered key ids and recognisable private parts."""

    def __init__(self) -> None:
        self.generated = 0

    def generate(self) -> GeneratedKey:
        self.generated += 1
        n = self.generated
        return GeneratedKey(
            f"kid-{n}",
            {"kty": "EC", "crv": "P-256", "x": f"x{n}", "y": f"y{n}", "use": "sig", "alg": "ES256"},
            f"-----BEGIN PRIVATE KEY-----\nprivate-{n}\n-----END PRIVATE KEY-----\n",
        )
