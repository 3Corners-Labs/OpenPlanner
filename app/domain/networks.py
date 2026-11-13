"""Addresses and networks: the one home for parsing, matching and counting callers (ADR 0124).

Three places used to keep their own copy of this: the machine API's allowed networks, the display
links' ACL and pin, and the pseudonym of a caller. They all need the same few rules, so they live
here and nowhere else:

    text ─► ``split_entries`` ─► ``parse_networks`` ─► canonical CIDR strings (once each, counted)
    text ─► ``address_of`` ─► IPv4 or IPv6 (an IPv4 address written as IPv6 is the IPv4 one)
    address + list ─► ``in_any`` ─► does one network hold it?
    address ─► ``caller_key`` ─► what a limiter counts it under: IPv4 as it is, IPv6 by its /64

The matching functions (``in_any``, ``each_within``) give no meaning to an empty list. What an
empty list stands for (any source, any network, none yet, a setting's choice) is the caller's own
rule and stays in the caller: the same word must never mean two things here and there. Pure
Python, no framework: the web layer passes the address the server derived.
"""

import ipaddress
from collections.abc import Iterable, Sequence

from app.domain.errors import DomainError, ErrorCode

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address

IPV6_PREFIX = 64
"""Prefix length under which an IPv6 address counts as one caller: one host owns a whole /64 and
can rotate within it. A fixed rule, not a setting."""

IPV4_PIN_PREFIX = 32
"""Prefix of the network an IPv4 address pins to (the address itself)."""

UNKNOWN_CALLER = "unknown"
"""The key (and the pseudonym) of a caller without an address."""

NETWORKS_FIELD = "networks"
"""``DomainError`` field of a refused list, so the page says which input was wrong."""


def split_entries(text: str) -> list[str]:
    """Return the entries of ``text``: one per line or comma separated."""
    return text.replace(",", "\n").splitlines()


def parse_networks(
    entries: Iterable[str], *, maximum: int, field: str = NETWORKS_FIELD
) -> tuple[str, ...]:
    """Return the networks of ``entries`` in canonical CIDR form, each once, in the given order.

    Args:
        entries: One network each; a single address is its own ``/32`` or ``/128``. Blank
            entries are skipped. Bits right of the prefix are cleared (``10.0.0.7/24`` is
            ``10.0.0.0/24``).
        maximum: How many different networks the caller allows.
        field: The field named in the error.

    Raises:
        DomainError: ``invalid_configuration`` for ``field`` when an entry is no network or there
            are more than ``maximum`` different ones.
    """
    found: list[str] = []
    for part in entries:
        value = part.strip()
        if not value:
            continue
        try:
            network = str(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, field) from None
        if network not in found:
            found.append(network)
    if len(found) > maximum:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, field)
    return tuple(found)


def _read(text: str | None) -> Address | None:
    """Return ``text`` as written, or ``None`` when it is no address."""
    try:
        return ipaddress.ip_address(text or "")
    except ValueError:
        return None


def address_of(text: str | None) -> Address | None:
    """Return ``text`` as an address, or ``None`` when it is none.

    An IPv4 address written as IPv6 (``::ffff:203.0.113.7``) is that IPv4 address: the same
    caller whichever way the network stack spelled it.
    """
    found = _read(text)
    if isinstance(found, ipaddress.IPv6Address) and found.ipv4_mapped is not None:
        return found.ipv4_mapped
    return found


def in_any(address: str | None, networks: Sequence[str]) -> bool:
    """Return whether ``address`` lies in one of ``networks``.

    An IPv4 address written as IPv6 is tried in both readings: as the IPv4 address against the
    IPv4 networks and as written against the IPv6 ones, so a list that names a mapped range
    (``::ffff:0:0/96``) or everything (``::/0``) still lets it in. An address of the other family
    matches nothing (no error), neither does a text that is no address.

    This gives no meaning to an empty list: it holds no address. Whether an empty list means
    "no restriction" is the caller's rule, never this function's.
    """
    written = _read(address)
    if written is None:
        return False
    readings: list[Address] = [written]
    if isinstance(written, ipaddress.IPv6Address) and written.ipv4_mapped is not None:
        readings.append(written.ipv4_mapped)
    return any(
        reading.version == net.version and reading in net
        for net in (ipaddress.ip_network(n) for n in networks)
        for reading in readings
    )


def each_within(inner: Sequence[str], outer: Sequence[str]) -> bool:
    """Return whether every network of ``inner`` lies within a network of ``outer``.

    Networks of another family are never within. This gives no meaning to an empty list: an empty
    ``inner`` is vacuously within anything and an empty ``outer`` holds only an empty ``inner``.
    What an empty list stands for is the caller's rule.
    """
    wide = [ipaddress.ip_network(n) for n in outer]
    return all(
        any(
            # The versions are compared first; mypy cannot narrow the pair of networks by that.
            candidate.version == net.version and candidate.subnet_of(net)  # type: ignore[arg-type]
            for net in wide
        )
        for candidate in (ipaddress.ip_network(n) for n in inner)
    )


def pinned_network(address: str | None) -> str | None:
    """Return the network an address pins to: its ``/32`` (IPv4) or ``/64`` (IPv6), else ``None``.

    The ``/64`` because privacy addresses rotate within it; an IPv4-mapped IPv6 address pins as
    the IPv4 address it is.
    """
    found = address_of(address)
    if found is None:
        return None
    prefix = IPV4_PIN_PREFIX if found.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network(f"{found}/{prefix}", strict=False))


def caller_key(address: str | None) -> str:
    """Return the key a limiter counts the caller at ``address`` under.

    An IPv4 address counts as it is, an IPv6 address by its ``/64``, so a caller that rotates
    addresses within its prefix keeps one key; an IPv4-mapped IPv6 address has the key of its IPv4
    form. The address is the one the server derived, never a header. The /64 rule holds for
    addresses only: a text that is no address (a test client's name) is its own key, as the raw
    text was before, and a missing address is ``UNKNOWN_CALLER``.
    """
    if not address:
        return UNKNOWN_CALLER
    found = address_of(address)
    if found is None:
        return address
    if isinstance(found, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((found, IPV6_PREFIX), strict=False))
    return str(found)
