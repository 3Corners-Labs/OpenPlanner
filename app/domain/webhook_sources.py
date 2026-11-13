"""Where a webhook may come from: the source-network rule of the gate (ADR 0124 points 3 and 4).

A connection carries a list of allowed networks, the installation a default per platform, and the
host may demand a list (``WEBHOOK_SOURCE_NETWORKS_REQUIRED``). This module holds the three
decisions that make of that one answer, all pure:

    own list, default list ─► ``networks_that_apply`` ─► the list that governs an address
    caller, list, switch, chain ─► ``check_source`` ─► allow, ``network`` or ``source_unknown``
    list, switch ─► ``source_state`` ─► what the portal says about the address (a finding)

What an empty list means is decided here and nowhere else: *any source*, except while the host
demands a list, when an address without one does not accept (``network``). ``app.domain.networks``
gives no meaning to an empty list; this module is the one place that does for webhooks.

The address is only as good as the proxy chain (point 4). A list that cannot be evaluated is
refused, not passed: ``ProxyChain`` says in which cases the caller's address cannot be trusted, and
every case reuses a judgement the application already makes of the proxy (``trusted_proxy``,
``classify_peer`` and ``forwarded_state`` of ``app.domain.public_address``); no second reading of
the chain lives here. The rule for the caller's address: it is held as the caller's only if
``FORWARDED_ALLOW_IPS`` is no wider than single hosts, this call did not arrive with forwarded
headers its peer was not trusted to send, and the address is neither a trusted proxy's nor a
loopback or private one while the public address is public. All of it is decided per call: the
proxy watch's standing sighting (ADR 0086) is a finding for System status and decides nothing
here, because a state that lasts would let any stranger switch every list off with one request.
An address without a list is never affected by it.
"""

import ipaddress
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError
from app.domain.inbound_gate import Refusal
from app.domain.networks import MAX_NETWORKS, address_of, in_any, parse_networks, split_entries
from app.domain.public_address import PeerClass, address_is_public, classify_peer, trusted_proxy

NETWORKS_FIELD = "allowed_networks"
"""``DomainError`` field of a refused list."""

WILDCARD = "*"
"""The entry of ``FORWARDED_ALLOW_IPS`` that trusts every peer."""


def networks_of(text: str, *, field: str = NETWORKS_FIELD) -> tuple[str, ...]:
    """Return the networks written in ``text`` (one per line or comma separated), canonical.

    Raises:
        DomainError: ``invalid_configuration`` for ``field`` when an entry is no network or there
            are more than ``MAX_NETWORKS`` different ones.
    """
    return parse_networks(split_entries(text), maximum=MAX_NETWORKS, field=field)


def valid_networks(text: str) -> bool:
    """Return whether ``text`` is a list of at most ``MAX_NETWORKS`` networks (may be empty)."""
    try:
        networks_of(text)
    except DomainError:
        return False
    return True


def networks_that_apply(own: tuple[str, ...], default: tuple[str, ...]) -> tuple[str, ...]:
    """Return the list that governs an address: the connection's own, else the installation's.

    An own list *replaces* the default (ADR 0124 point 3); an empty one falls back to it.
    """
    return own or default


class ChainFault(StrEnum):
    """Why the caller's address cannot be trusted; one word each, shown with what to repair."""

    WIDE_TRUST = "wide_trust"
    """``FORWARDED_ALLOW_IPS`` is ``*`` or wider than a host: any peer could say who it is."""
    UNTRUSTED_HOP = "untrusted_hop"
    """This very call carries forwarded headers that the server ignored: the address it holds is
    a hop that is not trusted. Decided per call; a header forged by someone else harms only
    that call."""
    CALLER_IS_PROXY = "caller_is_proxy"
    """The caller's address is one of the trusted proxies: the proxy did not hand the caller on."""
    HOP_NOT_HANDED_ON = "hop_not_handed_on"
    """The caller is a loopback or private address although the public address is public."""
    NO_ADDRESS = "no_address"
    """The server derived no address for the caller."""


def _wide(entry: str) -> bool:
    """Return whether one entry of ``FORWARDED_ALLOW_IPS`` trusts more than a single host."""
    if entry == WILDCARD:
        return True
    try:
        network = ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return False  # a literal (a socket path) names exactly one peer
    return network.prefixlen < network.max_prefixlen


def _networks(entries: tuple[str, ...]) -> tuple[str, ...]:
    """Return the entries that are addresses or networks, as canonical networks."""
    found: list[str] = []
    for entry in entries:
        try:
            found.append(str(ipaddress.ip_network(entry, strict=False)))
        except ValueError:
            continue
    return tuple(found)


@dataclass(frozen=True)
class ProxyChain:
    """What is known about the path from the caller to the application (ADR 0124 point 4).

    Attributes:
        trusted: The peers the server takes the caller's address from (``FORWARDED_ALLOW_IPS``,
            or the bundled proxy's address); empty without a proxy.
        address_is_public: Whether the address the supplier calls is a public one, so that a
            caller that is loopback or private did not come from the supplier.
        headers_ignored: Whether *this call* carries forwarded headers that the server did not
            use because its peer is not a trusted proxy (``forwarded_state`` is ``ignored``): the
            address held for the call is a hop that is not trusted. Per call, never a standing
            state: the proxy watch's sighting of ADR 0086 is a finding, not a gate decision.
    """

    trusted: tuple[str, ...]
    address_is_public: bool
    headers_ignored: bool = False

    @classmethod
    def of(
        cls,
        *,
        forwarded_allow_ips: str | None,
        tls_proxy_bundled: bool,
        webhook_base_url: str,
        headers_ignored: bool = False,
    ) -> "ProxyChain":
        """Return the chain as the settings and this call's forwarded headers describe it.

        The one reading of the proxy: the trusted peers are those ``trusted_proxy`` names (also
        what System status shows), the address is the one webhooks are registered at.
        """
        return cls(
            trusted_proxy(forwarded_allow_ips, tls_proxy_bundled=tls_proxy_bundled),
            address_is_public(webhook_base_url),
            headers_ignored,
        )

    def standing_faults(self) -> tuple[ChainFault, ...]:
        """Return the faults that make every caller's address untrustworthy, if any.

        Only what the installation's own settings say (a trust wider than single hosts); the
        faults of one call are told by ``fault_for``.
        """
        if any(_wide(entry) for entry in self.trusted):
            return (ChainFault.WIDE_TRUST,)
        return ()

    def fault_for(self, caller: str | None) -> ChainFault | None:
        """Return why the address of ``caller`` cannot be trusted, or ``None`` when it can.

        Args:
            caller: The address the server derived; never read from a header here.
        """
        standing = self.standing_faults()
        if standing:
            return standing[0]
        if self.headers_ignored:
            return ChainFault.UNTRUSTED_HOP
        if address_of(caller) is None:
            return ChainFault.NO_ADDRESS
        if in_any(caller, _networks(self.trusted)):
            return ChainFault.CALLER_IS_PROXY
        if self.address_is_public and classify_peer(caller) is not PeerClass.PUBLIC:
            return ChainFault.HOP_NOT_HANDED_ON
        return None


def check_source(
    caller: str | None, networks: tuple[str, ...], *, required: bool, chain: ProxyChain
) -> Refusal | None:
    """Decide whether the source of a call is acceptable (ADR 0124 points 3 and 4).

    Args:
        caller: The caller's address as the server derived it.
        networks: The list that applies (``networks_that_apply``); empty: none.
        required: ``WEBHOOK_SOURCE_NETWORKS_REQUIRED``: an address without a list does not accept.
        chain: What is known about the proxy chain.

    Returns:
        ``None`` to go on; ``Refusal.NETWORK`` for a caller outside the list, or any caller
        while a list is required and missing; ``Refusal.SOURCE_UNKNOWN`` when a list applies
        and the caller's address cannot be trusted. A call without a list is not asked about the
        chain.
    """
    if not networks:
        return Refusal.NETWORK if required else None
    if chain.fault_for(caller) is not None:
        return Refusal.SOURCE_UNKNOWN
    return None if in_any(caller, networks) else Refusal.NETWORK


class SourceState(StrEnum):
    """What the portal says about one address's source networks (ADR 0124 points 3 and 13)."""

    UNRESTRICTED = "unrestricted"
    """No list applies: any source may call. Shown, never a silent state."""
    RESTRICTED = "restricted"
    """A list applies."""
    REQUIRED_MISSING = "required_missing"
    """The host demands a list and none applies: the address does not accept."""


def source_state(networks: tuple[str, ...], *, required: bool) -> SourceState:
    """Return the state of an address from the list that applies and the host's switch."""
    if networks:
        return SourceState.RESTRICTED
    return SourceState.REQUIRED_MISSING if required else SourceState.UNRESTRICTED
