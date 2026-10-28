"""OpenID Connect adapter for the ``IdentityProvider`` port (ADR 0013).

Authorization code flow with PKCE (S256), ``state`` and ``nonce``. httpx makes the three calls
(discovery, token, JWKS); PyJWT verifies the ID token. Only asymmetric algorithms are accepted.
Which claims carry roles, organisations and groups is configuration (``ClaimMapping``), so the
same code serves PingFederate in production and Keycloak in development.

    start_login ──► authorization_endpoint?…&state&nonce&code_challenge
    finish_login ─► token_endpoint (code + code_verifier, client_secret_basic)
                 ─► verify id_token: signature (JWKS, kid), iss, aud/azp, exp, iat, nonce
                 ─► claims ──ClaimMapping──► Principal
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
import structlog

from app.domain.authorization import Principal, Role
from app.domain.ports import IdentityError, LoginStart
from app.domain.roles import MAX_ROLE_CLAIMS, is_claim_value

log = structlog.get_logger()

ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
"""Asymmetric only: ``none`` and HMAC (``HS*``) are never accepted."""

METADATA_TTL_SECONDS = 3600


@dataclass(frozen=True)
class ClaimMapping:
    """Which ID-token claims describe an admin, and which values mean which role.

    Attributes:
        role_claim: Claim with the role values (a list or a space/comma separated string).
        organisation_claim: Claim with organisation slugs (for org_admin).
        group_claim: Claim with group slugs (for group_admin).
        role_values: Claim value per role, e.g. ``{"openplanner-superuser": SUPERUSER}``.
    """

    role_claim: str
    organisation_claim: str
    group_claim: str
    role_values: Mapping[str, Role]


@dataclass(frozen=True)
class OidcConfig:
    """Client registration at the identity provider."""

    discovery_url: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: str
    mapping: ClaimMapping
    leeway_seconds: int = 60


def _values(claim: object) -> frozenset[str]:
    """Return a claim as a set of strings: lists, or space/comma separated text."""
    if isinstance(claim, str):
        return frozenset(v for v in claim.replace(",", " ").split() if v)
    if isinstance(claim, list | tuple):
        return frozenset(str(v) for v in claim if isinstance(v, str | int))
    return frozenset()


def principal_from_claims(claims: Mapping[str, Any], mapping: ClaimMapping) -> Principal:
    """Map verified ID-token claims to a ``Principal``.

    Values of built-in roles become roles. The other role values are kept as
    ``role_claims`` (custom roles link to them, ADR 0039): only values shaped like a claim
    value, at most ``MAX_ROLE_CLAIMS`` of them in a stable order, so the session stays small.
    """
    values = _values(claims.get(mapping.role_claim))
    roles = frozenset(mapping.role_values[v] for v in values if v in mapping.role_values)
    others = sorted(v for v in values if v not in mapping.role_values and is_claim_value(v))
    name = claims.get("name") or claims.get("preferred_username") or claims["sub"]
    return Principal(
        subject=str(claims["sub"]),
        display_name=str(name)[:200],
        roles=roles,
        organisations=_values(claims.get(mapping.organisation_claim)),
        groups=_values(claims.get(mapping.group_claim)),
        role_claims=frozenset(others[:MAX_ROLE_CLAIMS]),
    )


def _code_challenge(verifier: str) -> str:
    """Return the PKCE S256 challenge for ``verifier`` (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class OidcIdentityProvider:
    """``IdentityProvider`` for any standards-compliant OIDC provider.

    Args:
        config: Client registration and claim mapping.
        http: Shared HTTP client (closed by the caller).
    """

    def __init__(self, config: OidcConfig, http: httpx.AsyncClient) -> None:
        """Store the configuration; metadata and keys are fetched lazily and cached."""
        self._config = config
        self._http = http
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._keys: jwt.PyJWKSet | None = None

    async def start_login(self, redirect_uri: str) -> LoginStart:
        """Prepare an authorisation request with fresh ``state``, ``nonce`` and PKCE."""
        metadata = await self._discover()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._config.client_id,
                "redirect_uri": redirect_uri,
                "scope": self._config.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": _code_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return LoginStart(f"{metadata['authorization_endpoint']}?{query}", state, nonce, verifier)

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        """Exchange the code, verify the ID token and return the admin it describes.

        Raises:
            IdentityError: ``token_exchange_failed``, ``invalid_token`` or ``nonce_mismatch``.
        """
        metadata = await self._discover()
        try:
            response = await self._http.post(
                metadata["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self._config.client_id, self._config.client_secret),
            )
            response.raise_for_status()
            id_token = response.json()["id_token"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("oidc.token_exchange_failed", error=type(exc).__name__)
            raise IdentityError("token_exchange_failed") from exc
        claims = await self._verify(id_token, metadata)
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise IdentityError("nonce_mismatch")
        return principal_from_claims(claims, self._config.mapping)

    async def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        """Return the RP-initiated logout URL (OIDC ``end_session_endpoint``), if offered."""
        try:
            metadata = await self._discover()
        except IdentityError:
            return None
        endpoint = metadata.get("end_session_endpoint")
        if not isinstance(endpoint, str):
            return None
        query = urlencode(
            {
                "client_id": self._config.client_id,
                "post_logout_redirect_uri": post_logout_redirect_uri,
            }
        )
        return f"{endpoint}?{query}"

    async def _verify(self, id_token: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Verify signature and standard claims of an ID token."""
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") not in ALLOWED_ALGORITHMS:
                raise jwt.InvalidAlgorithmError(str(header.get("alg")))
            key = await self._signing_key(str(header.get("kid", "")), metadata)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._config.client_id,
                issuer=metadata["issuer"],
                leeway=self._config.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, KeyError, ValueError, httpx.HTTPError) as exc:
            log.warning("oidc.invalid_token", error=type(exc).__name__)
            raise IdentityError("invalid_token") from exc
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        azp = claims.get("azp")
        if (len(audiences) > 1 or azp is not None) and azp != self._config.client_id:
            log.warning("oidc.invalid_token", error="azp")
            raise IdentityError("invalid_token")
        return claims

    async def _signing_key(self, kid: str, metadata: Mapping[str, Any]) -> jwt.PyJWK:
        """Return the JWKS key with ``kid``, refreshing the key set once (rotation)."""
        for refresh in (False, True):
            if self._keys is None or refresh:
                response = await self._http.get(metadata["jwks_uri"])
                response.raise_for_status()
                self._keys = jwt.PyJWKSet.from_dict(response.json())
            for key in self._keys.keys:
                if key.key_id == kid:
                    return key
        raise jwt.InvalidKeyError("unknown kid")

    async def _discover(self) -> dict[str, Any]:
        """Return the provider metadata (cached for an hour).

        Raises:
            IdentityError: ``discovery_failed`` if the document cannot be fetched.
        """
        if (
            self._metadata is not None
            and time.monotonic() - self._metadata_at < METADATA_TTL_SECONDS
        ):
            return self._metadata
        try:
            response = await self._http.get(self._config.discovery_url)
            response.raise_for_status()
            metadata: dict[str, Any] = response.json()
            for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(metadata.get(key), str):
                    raise ValueError(key)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("oidc.discovery_failed", error=type(exc).__name__)
            raise IdentityError("discovery_failed") from exc
        self._metadata, self._metadata_at = metadata, time.monotonic()
        return metadata
