"""OpenID Connect adapter for the ``IdentityProvider`` port (ADR 0013).

Authorization code flow with PKCE (S256), ``state`` and ``nonce``. httpx makes the three calls
(discovery, token, JWKS); PyJWT verifies the ID token. Only asymmetric algorithms are accepted.
Which claims carry roles, organisations, groups and entitlements is configuration
(``ClaimMapping``), so the same code serves PingFederate in production and Keycloak in
development. The adapter only extracts (``LoginClaims``); what a value means is decided by
``app.domain.entitlements.resolve`` with the directory (ADR 0049 point 3).

    start_login ──► authorization_endpoint?…&state&nonce&code_challenge
    finish_login ─► token_endpoint (code + code_verifier; client_secret_basic, or the
                    private_key_jwt assertion of RFC 7523 for an organisation provider)
                 ─► verify id_token: signature (JWKS, kid), iss, aud/azp, exp, iat, nonce
                 ─► claims ──ClaimMapping──► LoginClaims

An organisation provider (ADR 0083) is registered in the portal, so its addresses are fetched
through the outbound guard (``Guard``); the platform provider of the environment is not.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx
import jwt
import structlog

from app.adapters.outbound import OutboundRefusedError, Resolver, guarded_request, system_resolver
from app.domain.authorization import Role
from app.domain.entitlements import LoginClaims
from app.domain.identity import PLATFORM, SubjectRefusedError, qualify
from app.domain.identity_providers import ASYMMETRIC_ALGORITHMS, ClientAuth, issuer_of
from app.domain.mfa import MFA_AMR_VALUES, MFA_REQUIRED, shows_mfa
from app.domain.ports import IdentityError, LoginStart, LoginTestReport
from app.domain.roles import MAX_ROLE_CLAIMS, is_claim_value
from app.domain.tunable import value_of

log = structlog.get_logger()

ALLOWED_ALGORITHMS = ASYMMETRIC_ALGORITHMS
"""Asymmetric only: ``none`` and HMAC (``HS*``) are never accepted."""

METADATA_TTL_SECONDS = 3600

ASSERTION_LIFETIME_SECONDS = 60
"""How long a ``private_key_jwt`` client assertion is valid (RFC 7523 §3: short)."""

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


@dataclass(frozen=True)
class Guard:
    """How a registered provider's addresses are fetched (ADR 0083 point 8).

    Attributes:
        allow_private: Relax the guard for a lab provider (development and test only).
        resolve: The name resolver (the system's; tests inject one).
    """

    allow_private: bool = False
    resolve: Resolver = system_resolver


@dataclass(frozen=True)
class ClaimMapping:
    """Which ID-token claims describe an admin, and which values mean which role.

    Attributes:
        role_claim: Claim with the role values (a list or a space/comma separated string).
        organisation_claim: Claim with organisation slugs (for org_admin).
        group_claim: Claim with group slugs (for group_admin).
        role_values: Claim value per role, e.g. ``{"openplanner-superuser": SUPERUSER}``.
        entitlement_claim: Claim with the entitlement values (ADR 0049).
        entitlement_prefix: Only values starting with ``<prefix>:`` are kept.
        max_entitlements: More values in the entitlement claim refuses the login.
    """

    role_claim: str
    organisation_claim: str
    group_claim: str
    role_values: Mapping[str, Role]
    entitlement_claim: str = "openplanner_entitlements"
    entitlement_prefix: str = "openplanner"
    max_entitlements: int = 200


@dataclass(frozen=True)
class OidcConfig:
    """Client registration at the identity provider.

    ``provider_key`` is empty for the platform provider of the environment and the
    organisation provider's key otherwise; it qualifies every subject (ADR 0083 point 16).
    ``client_auth`` picks the token-endpoint authentication: ``client_secret_basic`` with
    ``client_secret``, or ``private_key_jwt`` with ``signing_key`` (key id and private PEM).
    ``guard`` is set for a registered provider, whose addresses came from the portal.
    ``expected_issuer`` is the issuer the discovery document and every ID token must carry,
    compared exactly (OIDC Discovery §4.3, Core §3.1.3.7); ``None`` derives it from
    ``discovery_url`` (``issuer_of``). A registered provider passes the issuer stored at
    registration, so a later change of its discovery document cannot move it.
    ``acr_values`` are requested at login when not empty; with ``require_mfa`` an ID token
    that shows no second factor (an ``amr`` out of ``mfa_amr_values``, or an ``acr`` out of
    ``mfa_acr_values``, which is ``acr_values`` when empty) is refused
    with ``mfa_required`` (ADR 0100). MFA itself is the provider's.
    """

    discovery_url: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: str
    mapping: ClaimMapping | Callable[[], ClaimMapping]
    leeway_seconds: int = 60
    provider_key: str = PLATFORM
    client_auth: ClientAuth = ClientAuth.CLIENT_SECRET_BASIC
    signing_key: tuple[str, str] | None = field(default=None, repr=False)
    guard: Guard | None = None
    expected_issuer: str | None = None
    require_mfa: bool = False
    acr_values: str = ""
    mfa_amr_values: str = " ".join(sorted(MFA_AMR_VALUES))
    mfa_acr_values: str = ""

    @property
    def issuer(self) -> str:
        """Return the issuer to pin: the configured one, else the one the discovery URL names."""
        return self.expected_issuer or issuer_of(self.discovery_url)


def _values(claim: object) -> frozenset[str]:
    """Return a claim as a set of strings: lists, or space/comma separated text."""
    if isinstance(claim, str):
        return frozenset(v for v in claim.replace(",", " ").split() if v)
    if isinstance(claim, list | tuple):
        return frozenset(str(v) for v in claim if isinstance(v, str | int))
    return frozenset()


def claim_mapping(values: Mapping[str, str], max_entitlements: int = 200) -> ClaimMapping:
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
        entitlement_claim=values.get("oidc_entitlement_claim", "openplanner_entitlements"),
        entitlement_prefix=values.get("oidc_entitlement_prefix", "openplanner"),
        max_entitlements=max_entitlements,
    )


def login_claims(
    claims: Mapping[str, Any],
    mapping: ClaimMapping,
    *,
    log_lockout: bool = True,
    provider: str = PLATFORM,
) -> LoginClaims:
    """Extract what the verified ID-token claims say about the admin.

    Values of built-in roles become roles. The other role values are kept as
    ``role_claims`` (custom roles link to them, ADR 0039): only values shaped like a claim
    value, at most ``MAX_ROLE_CLAIMS`` of them in a stable order, so the session stays small.
    Of the entitlement claim only the values with the prefix are kept (ADR 0049 point 5). A
    login that resolves to no role at all is logged for the operator (``_log_lockout``).
    The subject is qualified with ``provider`` (ADR 0083 point 16).

    Raises:
        IdentityError: ``too_many_entitlements`` when the entitlement claim holds more values
            than ``mapping.max_entitlements`` (ADR 0049 point 6: fail closed);
            ``reserved_subject``, ``subject_too_long`` or ``empty_subject`` when the ``sub``
            cannot become an identity.
    """
    try:
        subject = qualify(provider, str(claims["sub"]))
    except SubjectRefusedError as refused:
        log.warning("oidc.subject_refused", reason=refused.reason)
        raise IdentityError(refused.reason) from refused
    seen = _values(claims.get(mapping.role_claim))
    roles = frozenset(mapping.role_values[value] for value in seen if value in mapping.role_values)
    others = sorted(v for v in seen if v not in mapping.role_values and is_claim_value(v))
    entitlements = _values(claims.get(mapping.entitlement_claim))
    if len(entitlements) > mapping.max_entitlements:
        log.warning("oidc.too_many_entitlements", actor=subject, values=len(entitlements))
        raise IdentityError("too_many_entitlements")
    head = f"{mapping.entitlement_prefix}:"
    name = claims.get("name") or claims.get("preferred_username") or claims["sub"]
    found = LoginClaims(
        subject=subject,
        display_name=str(name)[:200],
        roles=roles,
        organisations=_values(claims.get(mapping.organisation_claim)),
        groups=_values(claims.get(mapping.group_claim)),
        role_claims=frozenset(others[:MAX_ROLE_CLAIMS]),
        entitlements=frozenset(v for v in entitlements if v.startswith(head)),
    )
    if log_lockout:  # a dry run's verdict is audited, not logged as a lockout
        _log_lockout(claims, mapping, found, len(seen))
    return found


MAX_LOGGED_CLAIMS = 50
MAX_CLAIM_NAME = 64


def _claim_names(claims: Mapping[str, Any]) -> list[str]:
    """Return the names of the claims present, sorted and bounded; never their values."""
    return sorted(str(name)[:MAX_CLAIM_NAME] for name in claims)[:MAX_LOGGED_CLAIMS]


def _log_lockout(
    claims: Mapping[str, Any], mapping: ClaimMapping, principal: LoginClaims, seen: int
) -> None:
    """Log why a login grants nothing (ADR 0042 §8 rule 4), so the operator can fix it.

    The log names the configured claim that was missing and the *names* of the claims that
    were present, and counts the role values that matched nothing. Claim values (names,
    addresses, role, organisation and entitlement values) are never logged.
    """
    if not principal.roles and not principal.entitlements:
        missing = None if mapping.role_claim in claims else mapping.role_claim
        log.warning(
            "oidc.no_role",
            actor=principal.subject,
            missing_claim=missing,
            role_values_seen=seen,
            entitlements_seen=len(_values(claims.get(mapping.entitlement_claim))),
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


def client_secret_basic(client_id: str, client_secret: str) -> str:
    """Return the ``Authorization`` value of OAuth ``client_secret_basic`` (RFC 6749 §2.3.1).

    Each credential is form-urlencoded on its own before they are joined with a colon and
    base64-encoded, so a colon, plus or percent sign in either survives the round trip.
    """
    pair = f"{quote_plus(client_id, safe='')}:{quote_plus(client_secret, safe='')}"
    return f"Basic {base64.b64encode(pair.encode()).decode()}"


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
        if self._config.acr_values:
            params["acr_values"] = self._config.acr_values
        if fresh:
            params |= {"prompt": "login", "max_age": "0"}
        query = urlencode(params)
        return LoginStart(f"{metadata['authorization_endpoint']}?{query}", state, nonce, verifier)

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginClaims:
        """Exchange the code, verify the ID token and return what it says about the admin.

        Raises:
            IdentityError: ``token_exchange_failed``, ``invalid_token``, ``nonce_mismatch``,
                ``mfa_required`` or ``too_many_entitlements``.
        """
        claims = await self._claims(code, redirect_uri, code_verifier, nonce)
        return login_claims(
            claims, value_of(self._config.mapping), provider=self._config.provider_key
        )

    async def finish_test_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginTestReport:
        """Return the claims and what arrived, for the guided test login (ADR 0083 point 9).

        Raises:
            IdentityError: As ``finish_login``.
        """
        claims = await self._claims(code, redirect_uri, code_verifier, nonce)
        found = login_claims(
            claims,
            value_of(self._config.mapping),
            log_lockout=False,
            provider=self._config.provider_key,
        )
        verified = claims.get("email_verified")
        email = claims.get("email")
        domain = (
            email.rpartition("@")[2].lower() if isinstance(email, str) and "@" in email else None
        )
        return LoginTestReport(
            found,
            tuple(_claim_names(claims)),
            verified if isinstance(verified, bool) else None,
            domain or None,
        )

    async def finish_trial_login(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        trial: Mapping[str, str],
    ) -> tuple[LoginClaims, LoginClaims]:
        """Return the claims under the current mapping and under the ``trial`` one (§8).

        Raises:
            IdentityError: As ``finish_login``.
        """
        claims = await self._claims(code, redirect_uri, code_verifier, nonce)
        mapping = value_of(self._config.mapping)
        provider = self._config.provider_key
        current = login_claims(claims, mapping, provider=provider)
        tried = claim_mapping(trial, mapping.max_entitlements)
        return current, login_claims(claims, tried, log_lockout=False, provider=provider)

    async def _claims(
        self, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> dict[str, Any]:
        """Exchange the code and return the verified ID-token claims.

        Raises:
            IdentityError: ``token_exchange_failed``, ``invalid_token`` or ``nonce_mismatch``.
        """
        metadata = await self._discover()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        auth: tuple[str, str] | None = None
        if self._config.client_auth is ClientAuth.PRIVATE_KEY_JWT:
            data["client_id"] = self._config.client_id
            data["client_assertion_type"] = ASSERTION_TYPE
            data["client_assertion"] = self._client_assertion(metadata["token_endpoint"])
        else:
            auth = (self._config.client_id, self._config.client_secret)
        try:
            response = await self._fetch("POST", metadata["token_endpoint"], data=data, auth=auth)
            response.raise_for_status()
            id_token = response.json()["id_token"]
        except (httpx.HTTPError, KeyError, ValueError, OutboundRefusedError) as exc:
            log.warning("oidc.token_exchange_failed", error=type(exc).__name__)
            raise IdentityError("token_exchange_failed") from exc
        claims = await self._verify(id_token, metadata)
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise IdentityError("nonce_mismatch")
        if self._config.require_mfa and not shows_mfa(
            claims.get("amr"),
            claims.get("acr"),
            (self._config.mfa_acr_values or self._config.acr_values).split(),
            self._config.mfa_amr_values.split(),
        ):
            log.warning("oidc.mfa_missing", provider=self._config.provider_key or PLATFORM)
            raise IdentityError(MFA_REQUIRED)
        return claims

    def _client_assertion(self, token_endpoint: str) -> str:
        """Return the signed client assertion of RFC 7523 / OIDC Core §9.

        ``iss`` and ``sub`` are the client id, ``aud`` the token endpoint, ``jti`` fresh, and
        the lifetime a minute; signed with the provider's current key (``ES256``, ``kid``).

        Raises:
            IdentityError: ``no_signing_key`` when the provider has none.
        """
        if self._config.signing_key is None:
            raise IdentityError("no_signing_key")
        kid, pem = self._config.signing_key
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self._config.client_id,
                "sub": self._config.client_id,
                "aud": token_endpoint,
                "jti": secrets.token_urlsafe(16),
                "iat": now,
                "exp": now + ASSERTION_LIFETIME_SECONDS,
            },
            pem,
            algorithm="ES256",
            headers={"kid": kid},
        )

    async def _fetch(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send a request to the provider: through the guard for a registered one.

        ``auth=(user, password)`` becomes a basic ``Authorization`` header, so the guard,
        which builds the request itself, sends the same thing httpx would.
        """
        auth: tuple[str, str] | None = kwargs.pop("auth", None)
        if auth is not None:
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                "authorization": client_secret_basic(*auth),
            }
        guard = self._config.guard
        if guard is None:
            return await self._http.request(method, url, **kwargs)
        return await guarded_request(
            self._http,
            method,
            url,
            resolve=guard.resolve,
            allow_private=guard.allow_private,
            **kwargs,
        )

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
                issuer=self._config.issuer,
                leeway=self._config.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, KeyError, ValueError, httpx.HTTPError, OutboundRefusedError) as exc:
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
                response = await self._fetch("GET", metadata["jwks_uri"])
                response.raise_for_status()
                self._keys = jwt.PyJWKSet.from_dict(response.json())
            for key in self._keys.keys:
                if key.key_id == kid:
                    return key
        raise jwt.InvalidKeyError("unknown kid")

    async def _discover(self) -> dict[str, Any]:
        """Return the provider metadata (cached for an hour).

        Raises:
            IdentityError: ``discovery_failed`` if the document cannot be fetched or announces
                another issuer than the pinned one.
        """
        if (
            self._metadata is not None
            and time.monotonic() - self._metadata_at < METADATA_TTL_SECONDS
        ):
            return self._metadata
        try:
            response = await self._fetch("GET", self._config.discovery_url)
            response.raise_for_status()
            metadata: dict[str, Any] = response.json()
            for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(metadata.get(key), str):
                    raise ValueError(key)
        except (httpx.HTTPError, ValueError, OutboundRefusedError) as exc:
            log.warning("oidc.discovery_failed", error=type(exc).__name__)
            raise IdentityError("discovery_failed") from exc
        if metadata["issuer"] != self._config.issuer:  # exact: no trailing-slash tolerance
            log.warning("oidc.issuer_mismatch")
            raise IdentityError("discovery_failed")
        self._metadata, self._metadata_at = metadata, time.monotonic()
        return metadata
