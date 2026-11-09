"""The display link of a wallboard: its token, its states and the address rules (ADR 0117 step 14).

A display link shows the wallboard on a screen nobody signs in at. It is a secret address, so
everything here is about it not being usable twice, not for long and not from anywhere:

    new_token ─► shown once ─► only ``sha256(token)`` is stored
    first request (exchange) ─► consumes the link, stores ``sha256(session secret)`` and, when
                               the link pins, the network of the address that asked
    every later request carries the session secret ─► link still active? address inside the
                               ACL and the pin?

States are derived (``status_of``), never stored. An address is an ``ipaddress`` value: the ACL is
a list of CIDR ranges, a pin is the ``/32`` (IPv4) or ``/64`` (IPv6, because privacy addresses
rotate) of the address seen at the exchange. The address comes from the proxy chain the way the
rate limiter has it (ADR 0086), never from a raw header, so this module takes it as a string.

No database and no framework: ``WallboardLinkService`` is the use case, the adapter the storage.
"""

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode

TOKEN_BYTES = 32
"""Random bytes of a link's token and of its session secret: not a setting, a crypto parameter."""
MAX_NETWORKS = 20
"""Ranges one ACL may hold (ADR 0117 point 20)."""
MAX_NAME = 80
"""Longest name of a screen."""
TOUCH_EVERY = timedelta(minutes=10)
"""How often a link's *last used* is written: a poll every few seconds must not write each time."""
REFUSAL_EVERY = timedelta(hours=1)
"""One refusal event per link per period: a screen on the wrong network asks every few seconds."""
NETWORKS_FIELD = "networks"
NAME_FIELD = "name"
DAYS_FIELD = "days"
ENABLED_FIELD = "enabled"
"""``DomainError`` fields of a refused link, so the page says which input was wrong."""


class LinkStatus(StrEnum):
    """Where a link is in its life, as the list shows it."""

    NOT_OPENED = "not_opened"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Refusal(StrEnum):
    """Why a request with a link or a screen session was refused; a code, never a value."""

    LINK = "link"
    """Unknown, used, expired or revoked: not told apart, so a guess learns nothing."""
    NETWORK = "network"
    """The address is outside the link's ACL."""
    PIN = "pin"
    """The address is not the one the link was pinned to."""
    OFF = "off"
    """The organisation has switched display links off."""


@dataclass(frozen=True)
class WallboardLink:
    """One display link as stored.

    Attributes:
        id: Row id; ``None`` before it is stored.
        organisation_id: The organisation.
        organisation_slug: Its slug.
        group_id: The group the board is about, ``None`` for the organisation's groups.
        group_slug: The group's slug.
        name: What the screen is called.
        token_hash: ``sha256`` of the token, hex. The token itself is never stored.
        session_hash: ``sha256`` of the screen's session secret once the link was used.
        created_by: Subject of the admin who made it.
        created_at: When.
        expires_at: When the link and its session end.
        used_at: When the first request consumed it, else ``None``.
        last_used_at: The latest request, written at most every ``TOUCH_EVERY``.
        revoked_at: When an admin revoked it.
        networks: The ACL as CIDR strings; empty means any network.
        pin: Whether the first address pins the session.
        pinned_network: The pinned range, once used.
        last_refused_at: When a refusal was last recorded, for the one-per-period rule.
    """

    id: int | None
    organisation_id: int
    organisation_slug: str
    group_id: int | None
    group_slug: str | None
    name: str
    token_hash: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    session_hash: str | None = None
    used_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    networks: tuple[str, ...] = ()
    pin: bool = False
    pinned_network: str | None = None
    last_refused_at: datetime | None = field(default=None, repr=False)

    def scope(self) -> Scope:
        """Return the scope the link opens: its group, or its organisation's groups."""
        if self.group_slug is not None:
            return Scope(groups=frozenset({(self.organisation_slug, self.group_slug)}))
        return Scope(organisations=frozenset({self.organisation_slug}))


def new_token() -> str:
    """Return a new random secret, 32 bytes as URL-safe text."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Return the ``sha256`` of ``token`` as hex: what is stored in its place."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same_hash(stored: str | None, token: str) -> bool:
    """Return whether ``token`` is the secret behind ``stored``, in constant time."""
    return stored is not None and hmac.compare_digest(stored, hash_token(token))


def status_of(link: WallboardLink, now: datetime) -> LinkStatus:
    """Return the state of ``link`` at ``now``; a revocation outranks an expiry."""
    if link.revoked_at is not None:
        return LinkStatus.REVOKED
    if now >= link.expires_at:
        return LinkStatus.EXPIRED
    return LinkStatus.ACTIVE if link.used_at is not None else LinkStatus.NOT_OPENED


def parse_networks(text: str) -> tuple[str, ...]:
    """Return the ranges of ``text`` (one per line or comma separated) in canonical form.

    A host address is its own ``/32`` or ``/128``; bits right of the prefix are cleared.

    Raises:
        DomainError: ``invalid_configuration`` (field ``networks``) for text that is no range or
            more than ``MAX_NETWORKS`` ranges.
    """
    found: list[str] = []
    for part in text.replace(",", "\n").splitlines():
        value = part.strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, NETWORKS_FIELD) from None
        if str(network) not in found:
            found.append(str(network))
    if len(found) > MAX_NETWORKS:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, NETWORKS_FIELD)
    return tuple(found)


def inside(inner: Sequence[str], outer: Sequence[str]) -> bool:
    """Return whether every range of ``inner`` lies within a range of ``outer``.

    An empty ``outer`` is any network, so everything is inside it; an empty ``inner`` is any
    network too, so it is inside only an empty ``outer``.
    """
    if not outer:
        return True
    if not inner:
        return False
    wide = [ipaddress.ip_network(n) for n in outer]
    return all(
        any(
            candidate.version == net.version and candidate.subnet_of(net)  # type: ignore[arg-type]
            for net in wide
        )
        for candidate in (ipaddress.ip_network(n) for n in inner)
    )


def pinned_range(address: str) -> str | None:
    """Return the range an address pins to: its ``/32`` (IPv4) or ``/64`` (IPv6), else ``None``."""
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return None
    prefix = 32 if found.version == 4 else 64
    return str(ipaddress.ip_network(f"{found}/{prefix}", strict=False))


def allowed_from(address: str, networks: Sequence[str]) -> bool:
    """Return whether ``address`` lies in one of ``networks`` (an empty list allows any)."""
    if not networks:
        return True
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(
        found.version == net.version and found in net
        for net in (ipaddress.ip_network(n) for n in networks)
    )


def refusal_for(link: WallboardLink, address: str) -> Refusal | None:
    """Return why a request from ``address`` is refused by the link's address rules, if it is.

    The ACL is checked first, then the pin; both apply at the exchange and on every request.
    """
    if not allowed_from(address, link.networks):
        return Refusal.NETWORK
    if link.pinned_network is not None and not allowed_from(address, (link.pinned_network,)):
        return Refusal.PIN
    return None
