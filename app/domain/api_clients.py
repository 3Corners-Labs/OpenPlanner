"""API clients of the machine API and their credentials (ADR 0023).

An API client is a contact-center bot that the superuser registered. It authenticates with
OAuth 2.0 client credentials: a public ``client_id`` and a secret, exchanged for a short-lived
opaque access token. Secrets and tokens are 256-bit random values; the database keeps only
their SHA-256, so a database copy cannot call the API. Comparisons use
``hmac.compare_digest``. The prefixes make leaked values recognisable to secret scanners.

    ops_<secret> ──POST /oauth/token──► opa_<token> (10 min) ──Bearer──► API call
"""

import hashlib
import hmac
import ipaddress
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.authorization import ApiScope, ClientGrant
from app.domain.errors import DomainError, ErrorCode

CLIENT_ID_PREFIX = "opc_"
SECRET_PREFIX = "ops_"  # noqa: S105  # nosec B105 - a public prefix
TOKEN_PREFIX = "opa_"  # noqa: S105  # nosec B105 - a public prefix
_RANDOM_BYTES = 32

ACCESS_TOKEN_LIFETIME = timedelta(minutes=10)
MAX_ACTIVE_TOKENS = 5
"""Live tokens per client; issuing one more revokes the oldest."""

SECRET_LIFETIME_DAYS = (90, 180, 365)
"""How long a client secret may live; the superuser picks one and rotates before it ends."""

SECRET_EXPIRY_WARNING = timedelta(days=30)
MAX_NAME_LENGTH = 80
MAX_NETWORKS = 20

_CLIENT_ID = re.compile(r"^opc_[A-Za-z0-9_-]{16,64}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def new_client_id() -> str:
    """Return a fresh public client identifier."""
    return CLIENT_ID_PREFIX + secrets.token_urlsafe(16)


def new_secret() -> str:
    """Return a fresh client secret (shown once to the superuser)."""
    return SECRET_PREFIX + secrets.token_urlsafe(_RANDOM_BYTES)


def new_access_token() -> str:
    """Return a fresh access token (given once to the client)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(_RANDOM_BYTES)


def credential_hash(value: str) -> str:
    """Return the hex SHA-256 of a secret or token, the only form that is stored."""
    return hashlib.sha256(value.encode()).hexdigest()


def credential_matches(value: str, stored_hash: str) -> bool:
    """Return whether ``value`` hashes to ``stored_hash``, in constant time."""
    return hmac.compare_digest(credential_hash(value), stored_hash)


def is_client_id(value: str) -> bool:
    """Return whether ``value`` has the shape of a client id (safe to log and audit)."""
    return bool(_CLIENT_ID.fullmatch(value))


def parse_networks(lines: Iterable[str]) -> tuple[str, ...]:
    """Normalise an allow-list of networks (CIDR or single addresses).

    Raises:
        DomainError: ``invalid_configuration`` for ``networks`` when an entry is not a
            network or there are too many.
    """
    networks: list[str] = []
    for line in lines:
        entry = line.strip()
        if not entry:
            continue
        try:
            networks.append(str(ipaddress.ip_network(entry, strict=False)))
        except ValueError as exc:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "networks") from exc
    if len(networks) > MAX_NETWORKS:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "networks")
    return tuple(dict.fromkeys(networks))


def address_allowed(networks: tuple[str, ...], address: str | None) -> bool:
    """Return whether ``address`` lies in one of ``networks``; no networks allows any."""
    if not networks:
        return True
    if address is None:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(network) for network in networks)


@dataclass(frozen=True)
class ApiSettings:
    """The superuser's switches for the machine API; both off until switched on.

    Attributes:
        enabled: Whether tokens are issued and accepted (the emergency stop when off).
        docs_public: Whether the OpenAPI spec and documentation page are served.
        updated_at: When a switch last changed, if ever.
    """

    enabled: bool = False
    docs_public: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ClientDraft:
    """What the superuser enters for a client. Build it with ``ClientDraft.create``."""

    name: str
    organisation: str
    groups: frozenset[str]
    scopes: frozenset[ApiScope]
    networks: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        name: str,
        organisation: str,
        groups: Iterable[str],
        scopes: Iterable[str],
        networks: Iterable[str],
    ) -> "ClientDraft":
        """Validate raw form input (the organisation's groups are checked by the service).

        Raises:
            DomainError: ``invalid_configuration`` naming the offending field.
        """
        clean = " ".join(name.split())
        if not clean or len(clean) > MAX_NAME_LENGTH or _CONTROL_CHARS.search(name):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "name")
        try:
            granted = frozenset(ApiScope(scope) for scope in scopes)
        except ValueError as exc:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "scopes") from exc
        if not granted:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "scopes")
        return cls(
            clean,
            organisation,
            frozenset(g for g in groups if g),
            granted,
            parse_networks(networks),
        )


@dataclass(frozen=True)
class ApiClient:
    """A registered bot.

    Attributes:
        id: Database id; ``None`` before it is stored.
        client_id: Public identifier, also the actor in audit events.
        name: Label for the superuser, e.g. "WxCC AI Agent Belastingen".
        organisation: Slug of the organisation it belongs to.
        groups: Group slugs it is narrowed to; empty means every group of the organisation.
        scopes: OAuth scopes it holds.
        networks: Allowed source networks (CIDR); empty allows any.
        enabled: Whether it may get and use tokens.
        secret_hash: SHA-256 of the current secret.
        secret_expires_at: After this, no new tokens are issued until the secret is rotated.
        created_at: When it was registered.
        last_token_at: When it last received a token, if ever.
    """

    id: int | None
    client_id: str
    name: str
    organisation: str
    groups: frozenset[str]
    scopes: frozenset[ApiScope]
    networks: tuple[str, ...]
    enabled: bool
    secret_hash: str = field(repr=False)
    secret_expires_at: datetime
    created_at: datetime
    last_token_at: datetime | None = None

    @property
    def grant(self) -> ClientGrant:
        """What the client may reach, for ``decide_client``."""
        return ClientGrant(self.client_id, self.organisation, self.groups, self.scopes)

    def secret_expiring(self, now: datetime) -> bool:
        """Whether the secret ends within ``SECRET_EXPIRY_WARNING`` (or has ended)."""
        return self.secret_expires_at - now <= SECRET_EXPIRY_WARNING


@dataclass(frozen=True)
class AccessToken:
    """An issued access token as stored: its hash, owner, scopes and lifetime."""

    token_hash: str = field(repr=False)
    client_id: str
    scopes: frozenset[ApiScope]
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSecret:
    """A client with its new secret, which exists only here and on the superuser's screen."""

    client: ApiClient
    secret: str = field(repr=False)


@dataclass(frozen=True)
class IssuedToken:
    """A token response: the token itself exists only here and in the HTTP response."""

    access_token: str = field(repr=False)
    expires_in: int
    scopes: frozenset[ApiScope]


@dataclass(frozen=True)
class ApiCaller:
    """An authenticated API request: the client and the scopes of its token."""

    client: ApiClient
    scopes: frozenset[ApiScope]
    expires_at: datetime

    @property
    def grant(self) -> ClientGrant:
        """The client's grant narrowed to the token's scopes."""
        return ClientGrant(
            self.client.client_id,
            self.client.organisation,
            self.client.groups,
            self.scopes & self.client.scopes,
        )


class ApiAuthError(Exception):
    """Authentication of a machine request failed.

    ``code`` is the OAuth error answered to the client (``invalid_client``, ``invalid_scope``,
    ``invalid_token``, ``insufficient_scope``, ``temporarily_unavailable``); ``reason`` is the
    finer cause for logs and the audit log (``unknown_client``, ``bad_secret``,
    ``secret_expired``, ``disabled``, ``network``, ``api_disabled``, ``expired``). Neither
    ever contains a secret or token.
    """

    def __init__(self, code: str, reason: str) -> None:
        """Store the code and the reason; the message is the code."""
        super().__init__(code)
        self.code = code
        self.reason = reason
