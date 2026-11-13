"""Addresses and networks: the one home for parsing, matching and counting callers (ADR 0124).

Three places used to keep their own copy of this: the machine API's allowed networks, the display
links' ACL and pin, and the pseudonym of a caller. They all need the same few rules, so they live
here and nowhere else:

    text ─► ``parse_networks`` ─► canonical CIDR strings (host bits cleared, once each, counted)
    address ─► ``address_of`` ─► IPv4 or IPv6 (an IPv4 address written as IPv6 is the IPv4 one)
    address + list ─► ``allowed_from`` ─► does one network hold it?
    address ─► ``caller_key`` ─► what a limiter counts it under: IPv4 as it is, IPv6 by its /64

An empty list of networks means nothing here: a list that holds nothing holds no address. What an
empty list stands for (any source, any network, none yet) is the caller's own rule and stays in
the caller. Pure Python, no framework: the web layer passes the address the server derived.
"""

import ipaddress
from collections.abc import Iterable, Sequence

from app.domain.errors import DomainError, ErrorCode

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address
type Network = ipaddress.IPv4Network | ipaddress.IPv6Network

IPV6_PREFIX = 64
"""Prefix length under which an IPv6 address counts as one caller: one host owns a whole /64 and
can rotate within it. A fixed rule, not a setting."""

IPV4_PIN_PREFIX = 32
"""Prefix of the network an IPv4 address pins to (the address itself)."""

UNKNOWN_CALLER = "unknown"
"""The key of a caller without an address."""

NETWORKS_FIELD = "networks"
"""``DomainError`` field of a refused list, so the page says which input was wrong."""


def parse_networks(
    entries: Iterable[str] | str, *, maximum: int, field: str = NETWORKS_FIELD
) -> tuple[str, ...]:
    """Return the networks of ``entries`` in canonical CIDR form, each once, in the given order.

    Args:
        entries: Entries (a single address is its own ``/32`` or ``/128``), or one text with the
            entries one per line or comma separated. Blank entries are skipped. Bits right of the
            prefix are cleared (``10.0.0.7/24`` is ``10.0.0.0/24``).
        maximum: How many different networks the caller allows.
        field: The field named in the error.

    Raises:
        DomainError: ``invalid_configuration`` for ``field`` when an entry is no network or there
            are more than ``maximum`` different ones.
    """
    parts = entries.replace(",", "\n").splitlines() if isinstance(entries, str) else entries
    found: list[str] = []
    for part in parts:
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


def address_of(text: str | None) -> Address | None:
    """Return ``text`` as an address, or ``None`` when it is none.

    An IPv4 address written as IPv6 (``::ffff:203.0.113.7``) is that IPv4 address: the same
    caller whichever way the network stack spelled it.
    """
    try:
        found = ipaddress.ip_address(text or "")
    except ValueError:
        return None
    if isinstance(found, ipaddress.IPv6Address) and found.ipv4_mapped is not None:
        return found.ipv4_mapped
    return found


def allowed_from(address: str | None, networks: Sequence[str]) -> bool:
    """Return whether ``address`` lies in one of ``networks``.

    An address of the other family does not match (it is no error), an address that is none
    matches nothing, and an empty list holds no address: the caller decides what that means.
    """
    found = address_of(address)
    if found is None:
        return False
    return any(
        found.version == net.version and found in net
        for net in (ipaddress.ip_network(n) for n in networks)
    )


def inside(inner: Sequence[str], outer: Sequence[str]) -> bool:
    """Return whether every network of ``inner`` lies within a network of ``outer``.

    Networks of another family are never inside. An empty ``inner`` is inside anything (there is
    nothing outside); an empty ``outer`` holds only an empty ``inner``.
    """
    wide = [ipaddress.ip_network(n) for n in outer]
    return all(
        any(
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


def key_of(address: Address) -> str:
    """Return what a caller is counted under: an IPv4 address as it is, an IPv6 one by its /64."""
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((address, IPV6_PREFIX), strict=False))
    return str(address)


def caller_key(address: str | None) -> str:
    """Return the key a limiter counts the caller at ``address`` under.

    A caller that rotates addresses within its IPv6 ``/64`` keeps one key, so it cannot get a
    fresh bucket per request; an IPv4-mapped IPv6 address has the key of its IPv4 form. The
    address is the one the server derived, never a header: an unreadable text (a test client)
    is its own key and a missing address is ``UNKNOWN_CALLER``.
    """
    if not address:
        return UNKNOWN_CALLER
    found = address_of(address)
    return address if found is None else key_of(found)
