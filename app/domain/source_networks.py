"""The list of allowed source networks: what may be written, and what may be read back (ADR 0124).

A connection carries a list of the networks its webhooks may come from, the installation a default
per platform. This leaf module holds the rules of such a list and nothing else (it imports only
``errors`` and ``networks``, so a connection's change can use it without a cycle):

    text ─► ``source_networks_of`` ─► canonical networks: at most 32, none that lets a whole
                                      address family in ("any source" called "restricted")
    stored value ─► ``read_stored_networks`` ─► a ``NetworkList``: the same rule, plus every
                                      entry already canonical; anything else is unreadable

What the list means for a call (an empty list, the host's switch, the proxy chain) is the gate's
rule, in ``app.domain.webhook_sources``.

*A whole address family*: a network of prefix length 0 (``0.0.0.0/0``, ``::/0``), and an IPv6
network that contains the whole IPv4-mapped range ``::ffff:0:0/96`` (``::/1``, ``::ffff:0:0/96``
itself), because matching also tries the mapped reading of an IPv4 caller, so such a network lets
every IPv4 caller in. The two halves of a family (``0.0.0.0/1``, ``128.0.0.0/1``) are not refused,
on purpose: how wide a list is, is the organisation's choice.
"""

import ipaddress

from app.domain.errors import DomainError, ErrorCode
from app.domain.networks import NetworkList, networks_of

MAX_WEBHOOK_NETWORKS = 32
"""The most networks one list of allowed webhook source networks holds, a connection's or an
installation's default (ADR 0124 point 3). A fixed rule: a longer list is a sign that the wrong
tool is being used."""

NETWORKS_FIELD = "allowed_networks"
"""``DomainError`` field of a refused list."""

WHOLE_FAMILY_PREFIX = 0
"""A network with this prefix length covers a whole address family (``0.0.0.0/0``, ``::/0``)."""

MAPPED_IPV4 = ipaddress.IPv6Network("::ffff:0:0/96")
"""Where an IPv4 address written as IPv6 lives; a network that holds all of it admits every IPv4
caller."""


def _lets_a_family_in(network: str) -> bool:
    """Return whether ``network`` covers a whole address family or all of ``MAPPED_IPV4``."""
    parsed = ipaddress.ip_network(network)
    if parsed.prefixlen == WHOLE_FAMILY_PREFIX:
        return True
    return isinstance(parsed, ipaddress.IPv6Network) and MAPPED_IPV4.subnet_of(parsed)


def source_networks_of(text: str, *, field: str = NETWORKS_FIELD) -> tuple[str, ...]:
    """Return the allowed source networks written in ``text``, canonical; the one validation.

    The card, the two class C defaults, the ``Settings`` validator and reading a stored list back
    all go through here.

    Raises:
        DomainError: ``invalid_configuration`` for ``field`` when an entry is no network, there
            are more than ``MAX_WEBHOOK_NETWORKS`` different ones, or one lets a whole address
            family in (a list that lets everything in is no list).
    """
    found = networks_of(text, maximum=MAX_WEBHOOK_NETWORKS, field=field)
    if any(_lets_a_family_in(n) for n in found):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, field)
    return found


def valid_source_networks(text: str) -> bool:
    """Return whether ``text`` is an acceptable list of source networks (may be empty)."""
    try:
        source_networks_of(text)
    except DomainError:
        return False
    return True


def read_stored_networks(value: object) -> NetworkList:
    """Read a stored list back; anything that cannot be read is unreadable, never empty.

    The caller decides what a missing field means (a record made before the field existed is an
    empty list). A value that is there must be a list of strings (so ``None`` or a text is
    unreadable) that the writing rule accepts *and* that are already canonical: what was parsed
    equals what was stored, entry by entry. So an entry that does not parse, a blank one, one with
    host bits, a duplicate, a whole family, too many entries or one that was not written by this
    module make the whole list unreadable.
    """
    unreadable = NetworkList(unreadable=True)
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        return unreadable
    try:
        parsed = source_networks_of("\n".join(value))
    except DomainError:
        return unreadable
    return NetworkList(parsed) if parsed == tuple(value) else unreadable
