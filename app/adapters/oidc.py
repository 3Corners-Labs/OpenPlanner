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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
import structlog

from app.domain.authorization import Principal, Role
from app.domain.ports import IdentityError, LoginStart
from app.domain.roles import MAX_ROLE_CLAIMS, is_claim_value
from app.domain.tunable import value_of

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
    mapping: ClaimMapping | Callable[[], ClaimMapping]
    leeway_seconds: int = 60


def _values(claim: object) -> frozenset[str]:
    """Return a claim as a set of strings: lists, or space/comma separated text."""
    if isinstance(claim, str):
        return frozenset(v for v in claim.replace(",", " ").split() if v)
    if isinstance(claim, list | tuple):
        return frozenset(str(v) for v in claim if isinstance(v, str | int))
    return frozenset()


def claim_mapping(values: Mapping[str, str]) -> ClaimMapping:
    """Return the mapping the login settings describe (``oidc_*`` keys, ADR 0042 §8)."""
    return ClaimMapping(
        role_claim=values["oidc_role_claim"],
        organisation_claim=values["oidc_organisation_claim"],
        group_claim=values["oidc_group_claim"],
        role_values={
            values["oidc_role_superuser"]: Role.SUPERUSER,
            values["oidc_role_org_admin"]: Role.ORG_ADMIN,
            values["oidc_role_group_admin"]: Role.GROUP_ADMIN,
            values["oidc_role_auditor"]: Role.AUDITOR,
            values["oidc_role_agent"]: Role.AGENT,
        },
    )


def principal_from_claims(
    claims: Mapping[str, Any], mapping: ClaimMapping, *, log_lockout: bool = True
) -> Principal:
    """Map verified ID-token claims to a ``Principal``.

    Values of built-in roles become roles. The other role values are kept as
    ``role_claims`` (custom roles link to them, ADR 0039): only values shaped like a claim
    value, at most ``MAX_ROLE_CLAIMS`` of them in a stable order, so the session stays small.
    A login that resolves to no built-in role, or to a scoped role without its scope claim, is
    logged for the operator (``_log_lockout``); custom roles are attached after this, so such a
    login may still hold one.
    """
    seen = _values(claims.get(mapping.role_claim))
    roles = frozenset(mapping.role_values[value] for value in seen if value in mapping.role_values)
    others = sorted(v for v in seen if v not in mapping.role_values and is_claim_value(v))
    name = claims.get("name") or claims.get("preferred_username") or claims["sub"]
    principal = Principal(
        subject=str(claims["sub"]),
        display_name=str(name)[:200],
        roles=roles,
        organisations=_values(claims.get(mapping.organisation_claim)),
        groups=_values(claims.get(mapping.group_claim)),
        role_claims=frozenset(others[:MAX_ROLE_CLAIMS]),
    )
    if log_lockout:  # a dry run's verdict is audited, not logged as a lockout
        _log_lockout(claims, mapping, principal, len(seen))
    return principal


MAX_LOGGED_CLAIMS = 50
MAX_CLAIM_NAME = 64


def _claim_names(claims: Mapping[str, Any]) -> list[str]:
    """Return the names of the claims present, sorted and bounded; never their values."""
    return sorted(str(name)[:MAX_CLAIM_NAME] for name in claims)[:MAX_LOGGED_CLAIMS]


def _log_lockout(
    claims: Mapping[str, Any], mapping: ClaimMapping, principal: Principal, seen: int
) -> None:
    """Log why a login grants nothing (ADR 0042 §8 rule 4), so the operator can fix it.

    The log names the configured claim that was missing and the *names* of the claims that
    were present, and counts the role values that matched nothing. Claim values (names,
    addresses, role and organisation values) are never logged.
    """
    if not principal.roles:
        missing = None if mapping.role_claim in claims else mapping.role_claim
        log.warning(
            "oidc.no_role",
            actor=principal.subject,
            missing_claim=missing,
            role_values_seen=seen,
            claims_present=_claim_names(claims),
        )
        return
    scope_claims = (
        (Role.ORG_ADMIN, principal.organisations, mapping.organisation_claim),
        (Role.GROUP_ADMIN, principal.groups, mapping.group_claim),
    )
    for role, scope, claim in scope_claims:
        if principal.roles == {role} and not scope:
            log.warning(
                "oidc.no_scope",
                actor=principal.subject,
                missing_claim=claim if claim not in claims else None,
                claims_present=_claim_names(claims),
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

    async def start_login(self, redirect_uri: str, *, fresh: bool = False) -> LoginStart:
        """Prepare an authorisation request with fresh ``state``, ``nonce`` and PKCE.

        ``fresh`` adds ``prompt=login`` and ``max_age=0`` (OIDC Core §3.1.2.1), so the admin
        authenticates again rather than through single sign-on.
        """
        metadata = await self._discover()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self._config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if fresh:
            params |= {"prompt": "login", "max_age": "0"}
        query = urlencode(params)
        return LoginStart(f"{metadata['authorization_endpoint']}?{query}", state, nonce, verifier)

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        """Exchange the code, verify the ID token and return the admin it describes.

        Raises:
            IdentityError: ``token_exchange_failed``, ``invalid_token`` or ``nonce_mismatch``.
        """
        claims = await self._claims(code, redirect_uri, code_verifier, nonce)
        return principal_from_claims(claims, value_of(self._config.mapping))

    async def finish_trial_login(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        trial: Mapping[str, str],
    ) -> tuple[Principal, Principal]:
        """Return the admin under the current mapping and under the ``trial`` one (§8).

        Raises:
            IdentityError: As ``finish_login``.
        """
        claims = await self._claims(code, redirect_uri, code_verifier, nonce)
        current = principal_from_claims(claims, value_of(self._config.mapping))
        return current, principal_from_claims(claims, claim_mapping(trial), log_lockout=False)

    async def _claims(
        self, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> dict[str, Any]:
        """Exchange the code and return the verified ID-token claims.

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
        return claims

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
