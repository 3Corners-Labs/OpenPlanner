"""The public address and its proxy: shape, derived links and the round trip (ADR 0086).

Pure data and rules, shared by the ports, the worker's ``AddressWatch``
(``app.domain.address_check``), the ``/api/v1/instance`` endpoint, System status and the CLI.
The judgement of a round trip is one word (``AddressFinding``), so the page, the system event
and the CLI say the same thing. No address of a visitor is ever kept here.
"""

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from app.domain.web_transport import BUNDLED_PROXY_ADDRESS

EVENT = "public_address"
"""Action of the system event about the public address's round trip (ADR 0086 point 2)."""

NONCE_BYTES = 16
"""Length of the probe nonce: 128 random bits (fixed, ADR 0086 *Fixed on purpose*)."""

NONCE_LIFETIME_SECONDS = 300
"""How long a stored nonce stays valid; one is pending at a time and used once."""

INSTANCE_PATH = "/api/v1/instance"
"""Where the instance endpoint lives; the probe appends ``?probe={nonce}``."""

CALLBACK_PATH = "/admin/callback"
"""The OIDC redirect path under the public address (the IdP registration must match it)."""


class Shape(StrEnum):
    """Which of the three installations this is, as far as the environment can tell."""

    BUNDLED = "bundled"
    OWN = "own"
    NONE = "none"


class AddressKind(StrEnum):
    """Which of the two addresses a check is about."""

    PUBLIC = "public"
    WEBHOOK = "webhook"


class Forwarded(StrEnum):
    """What the app did with the forwarded headers of the request that carried the probe."""

    HONOURED = "honoured"
    IGNORED = "ignored"
    ABSENT = "absent"


class AddressFinding(StrEnum):
    """The one-word judgement of a round trip (ADR 0086 point 2)."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    OTHER_INSTANCE = "other_instance"
    NOT_HTTPS = "not_https"
    PROXY_NOT_TRUSTED = "proxy_not_trusted"
    NO_PROXY_HEADERS = "no_proxy_headers"
    REDIRECTED = "redirected"


@dataclass(frozen=True)
class InstanceAnswer:
    """What ``/api/v1/instance`` said.

    Attributes:
        instance_id: The installation's random id; every caller gets it.
        scheme: The scheme the app saw (``https`` or ``http``); only with the pending nonce.
        forwarded: Whether the forwarded headers were honoured; only with the pending nonce.
    """

    instance_id: str
    scheme: str | None = None
    forwarded: Forwarded | None = None


@dataclass(frozen=True)
class InstanceReply:
    """What the probe got back, or why it got nothing.

    Attributes:
        answer: The parsed answer; ``None`` without a usable one.
        redirected: The address answered with a redirect (to another name or scheme).
        error: Why no usable answer came: ``timeout``, ``unreachable``, ``tls``, ``http_<n>``,
            ``unreadable``. Provider or network words, never a body.
    """

    answer: InstanceAnswer | None = None
    redirected: bool = False
    error: str = ""


@dataclass(frozen=True)
class AddressCheck:
    """The stored outcome of the latest round trip to one address.

    Attributes:
        kind: Which address.
        checked_at: When the probe ran (UTC).
        url: The origin probed (scheme, host and port; no path, no query, no nonce).
        finding: The judgement.
        scheme: The scheme the app saw, when it told.
        forwarded: What the app did with the forwarded headers, when it told.
        error: The network or provider reason of an ``unreachable`` finding.
    """

    kind: AddressKind
    checked_at: datetime
    url: str
    finding: AddressFinding
    scheme: str | None = None
    forwarded: Forwarded | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """Return whether the address reaches this installation as a participant needs."""
        return self.finding is AddressFinding.OK


def shape_of(*, tls_proxy_bundled: bool, forwarded_allow_ips: str | None) -> Shape:
    """Return the installation's shape from ``TLS_PROXY_BUNDLED`` and ``FORWARDED_ALLOW_IPS``."""
    if tls_proxy_bundled:
        return Shape.BUNDLED
    return Shape.OWN if _trusted(forwarded_allow_ips) else Shape.NONE


def trusted_proxy(forwarded_allow_ips: str | None, *, tls_proxy_bundled: bool) -> tuple[str, ...]:
    """Return the proxy addresses uvicorn trusts, as the page shows them."""
    named = _trusted(forwarded_allow_ips)
    if named:
        return named
    return (BUNDLED_PROXY_ADDRESS,) if tls_proxy_bundled else ()


def _trusted(forwarded_allow_ips: str | None) -> tuple[str, ...]:
    """Split the raw comma separated value."""
    return tuple(p.strip() for p in (forwarded_allow_ips or "").split(",") if p.strip())


def origin_of(url: str) -> str:
    """Return ``scheme://host[:port]`` of ``url``, without path, query or credentials."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def url_on(public_base_url: str, path: str) -> str:
    """Return ``path`` on the public address: the one way a link is built (ADR 0086 point 4)."""
    return f"{public_base_url.rstrip('/')}{path}"


def nonce_hash(nonce: str) -> str:
    """Return the ``sha256`` of a probe nonce; the database keeps only this."""
    return hashlib.sha256(nonce.encode()).hexdigest()


def nonce_matches(stored: str | None, offered: str) -> bool:
    """Compare an offered nonce with the stored hash in constant time."""
    if not stored or not offered:
        return False
    return hmac.compare_digest(stored, nonce_hash(offered))


def judge(reply: InstanceReply, *, instance_id: str, public_https: bool) -> AddressFinding:
    """Judge one round trip (ADR 0086 point 2).

    Args:
        reply: What the probe got back.
        instance_id: This installation's id.
        public_https: Whether the address under test says ``https``.

    Returns:
        ``ok`` only when the name reaches this installation and, for an ``https`` address,
        the app saw ``https`` and honoured the forwarded headers.
    """
    if reply.redirected:
        return AddressFinding.REDIRECTED
    answer = reply.answer
    if answer is None:
        return AddressFinding.UNREACHABLE
    if answer.instance_id != instance_id:
        return AddressFinding.OTHER_INSTANCE
    if answer.scheme is None or answer.forwarded is None:
        # Our id but not the detail: the nonce was not accepted, which is a different process.
        return AddressFinding.OTHER_INSTANCE
    if not public_https:
        return AddressFinding.NOT_HTTPS
    if answer.forwarded is Forwarded.IGNORED:
        return AddressFinding.PROXY_NOT_TRUSTED
    if answer.forwarded is Forwarded.ABSENT:
        return AddressFinding.NO_PROXY_HEADERS
    if answer.scheme != "https":
        return AddressFinding.NOT_HTTPS
    return AddressFinding.OK


def forwarded_state(
    *, forwarded_for: str | None, forwarded_proto: str | None, client: str | None, scheme: str
) -> Forwarded:
    """Say what the app did with the forwarded headers of one request.

    uvicorn rewrites the client address and the scheme only when the peer is trusted. So the
    headers were *honoured* when the client address is one of the hops of ``X-Forwarded-For``
    (or the scheme equals ``X-Forwarded-Proto``), *ignored* when they were there and the app
    kept the peer's values, and *absent* when there were none.

    Args:
        forwarded_for: The raw ``X-Forwarded-For`` header, if any.
        forwarded_proto: The raw ``X-Forwarded-Proto`` header, if any.
        client: The client address the app sees (after uvicorn's rewriting).
        scheme: The scheme the app sees.
    """
    if not forwarded_for and not forwarded_proto:
        return Forwarded.ABSENT
    if forwarded_for:
        hops = {hop.strip() for hop in forwarded_for.split(",")}
        if client is not None and client in hops:
            return Forwarded.HONOURED
        return Forwarded.IGNORED
    wanted = (forwarded_proto or "").split(",")[0].strip().lower()
    return Forwarded.HONOURED if wanted == scheme else Forwarded.IGNORED


class PeerClass(StrEnum):
    """What kind of address a peer is: the only thing about it that is ever shown."""

    LOOPBACK = "loopback"
    PRIVATE = "private"
    PUBLIC = "public"


def classify_peer(address: str | None) -> PeerClass:
    """Return the class of a peer address; anything unparsable counts as public."""
    try:
        parsed = ipaddress.ip_address(address or "")
    except ValueError:
        return PeerClass.PUBLIC
    if parsed.is_loopback:
        return PeerClass.LOOPBACK
    return PeerClass.PUBLIC if parsed.is_global else PeerClass.PRIVATE
