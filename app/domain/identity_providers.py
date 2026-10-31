"""Organisation identity providers: registration, keys and the discovery check (ADR 0083).

An organisation provider is an OpenID Connect provider the superuser registers for exactly
one organisation (points 2 to 7). What the superuser enters is a ``ProviderDraft``; the
stored registration is an ``OrganisationProvider``. The client authenticates at the token
endpoint with ``private_key_jwt`` (RFC 7523, the default of the NL GOV profile) using a key
pair OpenPlanner generates per provider, or with ``client_secret_basic`` and a secret that is
stored encrypted and never read back into the domain (ADR 0008).

The discovery document is checked when the registration is saved (point 7): valid OIDC
Discovery, its ``issuer`` the base of the discovery URL, the ``code`` flow, ``S256``, an
asymmetric ID-token signature and, for ``private_key_jwt``, that method announced.

    ProviderDraft.create(...) ──validate──► draft ──discovery check──► OrganisationProvider
    GeneratedKey (kid, public JWK, private PEM) ──sealed by the adapter──► SigningKey (public)
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from app.domain.entitlements import IDP_KEY_PATTERN, PREFIX_PATTERN
from app.domain.errors import DomainError, ErrorCode

DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
"""Where OIDC Discovery 1.0 §4 publishes the provider metadata, under the issuer."""

ASYMMETRIC_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
"""ID-token signatures the portal accepts: never ``none`` and never HMAC (ADR 0013)."""

DEFAULT_SCOPES = "openid profile email"
"""Scopes asked of an organisation provider; ``email`` so the domain can be confirmed."""

KEY_OVERLAP = timedelta(hours=24)
"""How long a retired signing key stays published after a rotation (point 6; fixed)."""

MAX_NAME_LENGTH = 80
MAX_URL_LENGTH = 500
MAX_CLIENT_ID_LENGTH = 255
MAX_SCOPES_LENGTH = 200
MAX_CLAIM_LENGTH = 64
MAX_SECRET_LENGTH = 512

_KEY = re.compile(IDP_KEY_PATTERN)
_PREFIX = re.compile(PREFIX_PATTERN)
_CLAIM = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")
"""One scope token (RFC 6749 §3.3): printable ASCII without space, quote and backslash."""
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ClientAuth(StrEnum):
    """How the client authenticates at the token endpoint (OIDC Core §9)."""

    PRIVATE_KEY_JWT = "private_key_jwt"
    """RFC 7523: a signed assertion with the provider's own key pair (the default)."""
    CLIENT_SECRET_BASIC = "client_secret_basic"  # noqa: S105  # nosec B105 - a method name
    """A client secret in the ``Authorization`` header, for providers without RFC 7523."""

    @property
    def needs_secret(self) -> bool:
        """Whether the method needs a stored client secret."""
        return self is ClientAuth.CLIENT_SECRET_BASIC

    @property
    def needs_key(self) -> bool:
        """Whether the method needs a signing key pair."""
        return self is ClientAuth.PRIVATE_KEY_JWT


def _invalid(name: str) -> DomainError:
    """Return the refusal for field ``name`` (never with its value)."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, name)


def _check_text(value: str, name: str, max_length: int) -> str:
    """Return ``value`` stripped; refuse an empty, too long or control-character text."""
    text = value.strip()
    if not text or len(text) > max_length or _CONTROL_CHARS.search(text):
        raise _invalid(name)
    return text


def is_discovery_url(value: str) -> bool:
    """Return whether ``value`` is an ``https`` discovery URL without query or fragment."""
    if len(value) > MAX_URL_LENGTH or not value.startswith("https://"):
        return False
    if any(c in value for c in "?#@ ") or _CONTROL_CHARS.search(value):
        return False
    base = value[: -len(DISCOVERY_SUFFIX)]
    return value.endswith(DISCOVERY_SUFFIX) and len(base) > len("https://")


def issuer_of(discovery_url: str) -> str:
    """Return the issuer a discovery URL belongs to (OIDC Discovery §4.1)."""
    return discovery_url[: -len(DISCOVERY_SUFFIX)]


def validate_scopes(value: str) -> str:
    """Return the scopes normalised to one space; ``openid`` must be among them."""
    scopes = value.split()
    if not scopes or "openid" not in scopes or len(" ".join(scopes)) > MAX_SCOPES_LENGTH:
        raise _invalid("scopes")
    if any(_SCOPE.fullmatch(s) is None for s in scopes):
        raise _invalid("scopes")
    return " ".join(dict.fromkeys(scopes))


def validate_secret(value: str) -> None:
    """Refuse an empty, too long or control-character client secret."""
    if not value or len(value) > MAX_SECRET_LENGTH or _CONTROL_CHARS.search(value):
        raise _invalid("client_secret")


@dataclass(frozen=True)
class ProviderDraft:
    """What the superuser enters for an organisation provider (point 5), validated.

    Attributes:
        key: The provider key (``IDP_KEY_PATTERN``), fixed after creation; it qualifies every
            subject of the provider (ADR 0083 point 16).
        name: Shown to admins, for example the organisation's name for its login service.
        discovery_url: The ``.well-known/openid-configuration`` address.
        client_id: The client registration at the provider.
        client_auth: How the client authenticates at the token endpoint.
        scopes: Space-separated, always with ``openid``.
        entitlement_claim: The claim that carries the entitlement values (ADR 0049).
        entitlement_prefix: The prefix of every entitlement value.
    """

    key: str
    name: str
    discovery_url: str
    client_id: str
    client_auth: ClientAuth
    scopes: str
    entitlement_claim: str
    entitlement_prefix: str

    @classmethod
    def create(  # noqa: PLR0913
        cls,
        *,
        key: str,
        name: str,
        discovery_url: str,
        client_id: str,
        client_auth: str,
        scopes: str,
        entitlement_claim: str,
        entitlement_prefix: str,
    ) -> "ProviderDraft":
        """Validate the submitted values.

        Raises:
            DomainError: ``invalid_configuration`` naming the field.
        """
        if _KEY.fullmatch(key) is None:
            raise _invalid("key")
        if not is_discovery_url(discovery_url.strip()):
            raise _invalid("discovery_url")
        try:
            auth = ClientAuth(client_auth)
        except ValueError as error:
            raise _invalid("client_auth") from error
        if _CLAIM.fullmatch(entitlement_claim) is None:
            raise _invalid("entitlement_claim")
        if _PREFIX.fullmatch(entitlement_prefix) is None:
            raise _invalid("entitlement_prefix")
        return cls(
            key=key,
            name=_check_text(name, "name", MAX_NAME_LENGTH),
            discovery_url=discovery_url.strip(),
            client_id=_check_text(client_id, "client_id", MAX_CLIENT_ID_LENGTH),
            client_auth=auth,
            scopes=validate_scopes(scopes),
            entitlement_claim=entitlement_claim,
            entitlement_prefix=entitlement_prefix,
        )


@dataclass(frozen=True)
class OrganisationProvider:
    """A registered organisation provider, as stored; never a secret or a private key.

    Attributes:
        id: Database id, ``None`` before it is stored.
        organisation: Slug of the organisation it belongs to.
        key: The provider key.
        name: The display name.
        discovery_url: The discovery address.
        issuer: The issuer the discovery document announced at the last check.
        client_id: The client registration.
        client_auth: The client authentication method.
        scopes: Space-separated scopes.
        entitlement_claim: The entitlement claim name.
        entitlement_prefix: The entitlement prefix.
        enabled: Whether the provider routes logins (step 3); off until the superuser
            switches it on after a test login.
        secret_set: Whether a client secret is stored (``client_secret_basic``).
        created_at: When it was registered.
        updated_at: When it last changed; adapters use it to notice a change.
        checked_at: When discovery was last checked successfully.
    """

    id: int | None
    organisation: str
    key: str
    name: str
    discovery_url: str
    issuer: str
    client_id: str
    client_auth: ClientAuth
    scopes: str
    entitlement_claim: str
    entitlement_prefix: str
    enabled: bool
    secret_set: bool
    created_at: datetime
    updated_at: datetime
    checked_at: datetime | None = None

    @property
    def needs_secret(self) -> bool:
        """Whether the method needs a stored client secret."""
        return self.client_auth.needs_secret

    @property
    def needs_key(self) -> bool:
        """Whether the method needs a signing key pair."""
        return self.client_auth.needs_key


@dataclass(frozen=True)
class GeneratedKey:
    """A fresh key pair from the ``SigningKeys`` adapter; the private part never persists here.

    Attributes:
        kid: The key id, published in the JWKS and sent in the assertion header.
        public_jwk: The public key as a JWK (``kty``, ``crv``, ``x``, ``y``, ``alg``, ``use``).
        private_pem: The private key in PEM; sealed by the adapter, shown to nobody.
    """

    kid: str
    public_jwk: Mapping[str, str]
    private_pem: str = field(repr=False)


@dataclass(frozen=True)
class SigningKey:
    """A stored key pair's public side.

    Attributes:
        provider_key: The provider it belongs to.
        kid: The key id.
        public_jwk: The public JWK.
        created_at: When it was generated.
        retired_at: When a rotation replaced it; ``None`` while it is the current key.
    """

    provider_key: str
    kid: str
    public_jwk: Mapping[str, str]
    created_at: datetime
    retired_at: datetime | None = None

    def published(self, now: datetime) -> bool:
        """Whether the key still belongs in the JWKS: current, or retired within the overlap."""
        return self.retired_at is None or now - self.retired_at < KEY_OVERLAP


def jwks(keys: list[SigningKey]) -> dict[str, list[dict[str, str]]]:
    """Return the JWK Set document (RFC 7517 §5) of ``keys``, in a stable order."""
    return {"keys": [{**dict(k.public_jwk), "kid": k.kid} for k in sorted(keys, key=_key_order)]}


def _key_order(key: SigningKey) -> tuple[str, str]:
    """Order keys by provider and id, so the document is the same on every read."""
    return key.provider_key, key.kid


class DiscoveryRefusedError(Exception):
    """The discovery document does not pass the check of ADR 0083 point 7.

    Args:
        reason: ``not_a_document``, ``missing_<field>``, ``issuer_mismatch``,
            ``no_code_flow``, ``no_pkce``, ``no_asymmetric_signature`` or
            ``auth_method_unsupported``; never a value from the document.
    """

    def __init__(self, reason: str) -> None:
        """Store the reason; it is also the message."""
        super().__init__(reason)
        self.reason = reason


def check_discovery(
    document: Mapping[str, Any], discovery_url: str, client_auth: ClientAuth
) -> str:
    """Check a discovery document and return its issuer (ADR 0083 point 7).

    Raises:
        DiscoveryRefusedError: With the reason the document is refused.
    """
    for name in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(document.get(name), str) or not document[name]:
            raise DiscoveryRefusedError(f"missing_{name}")
    issuer = str(document["issuer"])
    if issuer.rstrip("/") != issuer_of(discovery_url).rstrip("/"):
        raise DiscoveryRefusedError("issuer_mismatch")
    if "code" not in _strings(document.get("response_types_supported")):
        raise DiscoveryRefusedError("no_code_flow")
    if "S256" not in _strings(document.get("code_challenge_methods_supported")):
        raise DiscoveryRefusedError("no_pkce")
    signing = _strings(document.get("id_token_signing_alg_values_supported"))
    if not signing & set(ASYMMETRIC_ALGORITHMS):
        raise DiscoveryRefusedError("no_asymmetric_signature")
    methods = document.get("token_endpoint_auth_methods_supported")
    announced = _strings(methods) if methods is not None else {ClientAuth.CLIENT_SECRET_BASIC}
    if client_auth.value not in announced:
        raise DiscoveryRefusedError("auth_method_unsupported")
    return issuer


def _strings(value: object) -> set[str]:
    """Return the strings of a JSON list; anything else is the empty set."""
    if isinstance(value, list):
        return {v for v in value if isinstance(v, str)}
    return set()
