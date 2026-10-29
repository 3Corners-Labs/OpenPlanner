"""Pseudonyms of callers for security events (ADR 0053 point 2).

A security event says which caller it came from, so the security team can tell one scanner
from many visitors, but an IP address is personal data and never stored. The event carries
an HMAC of the address under a server key instead: the same address gives the same pseudonym
on every event of this installation, and without the key nobody can turn it back.

    203.0.113.7            ─► HMAC-SHA256(key, "203.0.113.7")[:16 hex]
    2001:db8:1:2::1        ─► HMAC-SHA256(key, "2001:db8:1:2::/64")[:16 hex]
    None, "testclient"     ─► "unknown"

IPv6 addresses count by their /64 prefix: one host owns a whole /64 and can rotate within it.
"""

import hashlib
import hmac
import ipaddress

PSEUDONYM_HEX = 16
"""Length of a pseudonym in hex characters (64 bits): fixed, a crypto parameter."""

IPV6_PREFIX = 64
"""Prefix length under which IPv6 addresses count as one caller."""

UNKNOWN_CLIENT = "unknown"
"""Pseudonym when the caller's address is missing or not an IP address."""

MANY_CLIENTS = "many"
"""Client under which events count once ``SecurityLog`` tracks its maximum of callers."""


class ClientPseudonyms:
    """Turns caller addresses into stable pseudonyms.

    Args:
        key: Server key; ``app.keys.client_pseudonym_key`` derives it.
    """

    def __init__(self, key: bytes) -> None:
        """Store the key."""
        self._key = key

    def of(self, address: str | None) -> str:
        """Return the pseudonym of ``address``, or ``unknown`` if it is not an IP address."""
        try:
            ip = ipaddress.ip_address(address or "")
        except ValueError:
            return UNKNOWN_CLIENT
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if isinstance(ip, ipaddress.IPv6Address):
            canonical = str(ipaddress.IPv6Network((ip, IPV6_PREFIX), strict=False))
        else:
            canonical = str(ip)
        digest = hmac.new(self._key, canonical.encode(), hashlib.sha256).hexdigest()
        return digest[:PSEUDONYM_HEX]
