"""The ``edge`` network of ``docker-compose.yml``: a visitor over IPv6 keeps their address.

Docker forwards a published port with NAT only to an address family the container's network has.
On the IPv4-only ``edge`` network of before, Docker's userland proxy took the IPv6 connections
and opened new ones over IPv4, so every visitor over IPv6 reached the proxy from the bridge's own
address: one rate limit and one caller pseudonym for all of them (seen on a dual-stack host on
5 November 2026). ``scripts/check-proxy.sh`` proves the forwarding on a real Docker
(``ipv6_client_check``); these tests keep the file's shape honest without one.
"""

import ipaddress
import re
from pathlib import Path
from typing import Any

import yaml

from app.domain.web_transport import BUNDLED_PROXY_ADDRESS

COMPOSE: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parents[1] / "docker-compose.yml").read_text()
)
EDGE: dict[str, Any] = COMPOSE["networks"]["edge"]
UNIQUE_LOCAL = ipaddress.IPv6Network("fc00::/7")
"""RFC 4193: addresses that nothing outside the site routes to."""


def _pools() -> dict[int, dict[str, str]]:
    """Return the edge network's address pools by IP version."""
    return {ipaddress.ip_network(pool["subnet"]).version: pool for pool in EDGE["ipam"]["config"]}


def test_the_edge_network_is_dual_stack_unless_the_kernel_has_no_ipv6() -> None:
    # The installer writes OPENPLANNER_EDGE_IPV6=false only for a kernel booted without IPv6.
    assert EDGE["enable_ipv6"] == "${OPENPLANNER_EDGE_IPV6:-true}"
    assert sorted(_pools()) == [4, 6]


def test_its_ipv6_subnet_is_one_unique_local_slash_64() -> None:
    subnet = ipaddress.IPv6Network(_pools()[6]["subnet"])

    assert subnet.prefixlen == 64
    assert subnet.subnet_of(UNIQUE_LOCAL)


def test_the_proxy_has_a_fixed_ipv4_address_only() -> None:
    """Web trusts that one address; the IPv6 one is dynamic and nothing may trust it.

    Docker Engine 29 refuses a fixed IPv6 address on a network without IPv6, so a fixed one
    would stop the proxy on a host with ``OPENPLANNER_EDGE_IPV6=false``.
    """
    pool = _pools()[4]
    address = ipaddress.IPv4Address(BUNDLED_PROXY_ADDRESS)

    assert COMPOSE["services"]["proxy"]["networks"]["edge"] == {"ipv4_address": str(address)}
    assert address in ipaddress.IPv4Network(pool["subnet"])
    assert address not in ipaddress.IPv4Network(pool["ip_range"])  # never handed to another


def test_web_listens_on_ipv4_only_so_the_proxy_arrives_from_the_trusted_address() -> None:
    """On ``::`` as well, the proxy could reach web over IPv6, from an address web ignores."""
    script = COMPOSE["services"]["web"]["command"][-1]

    assert re.search(r"--host 0\.0\.0\.0 ", script)
    assert f"FORWARDED_ALLOW_IPS:-{BUNDLED_PROXY_ADDRESS}}}" in script
