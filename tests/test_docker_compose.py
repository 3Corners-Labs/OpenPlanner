"""The ``edge`` network of ``docker-compose.yml``: a visitor over IPv6 keeps their address.

Docker forwards a published port with NAT only to an address family the container's network has.
On the IPv4-only ``edge`` network of before, Docker's userland proxy took the IPv6 connections
and opened new ones over IPv4, so every visitor over IPv6 reached the proxy from the bridge's own
address: one rate limit and one caller pseudonym for all of them (seen on a dual-stack host on
5 November 2026). ``scripts/check-proxy.sh`` proves the forwarding on a real Docker
(``ipv6_client_check``); these tests keep the file's shape honest without one.

The hops on one host go to fixed addresses, never to a name the host's resolver could answer
while a container is down (ADR 0125 step 2): the proxy to web and to the bundled identity
provider, web and worker to that provider, the provider to its database. The real-Docker proof is
``scripts/check-proxy.sh`` pass 4; here every place that names those addresses is held equal.
"""

import ipaddress
import re
from typing import Any

from app.domain.web_transport import (
    BUNDLED_IDP_ADDRESS,
    BUNDLED_IDP_UPSTREAM,
    BUNDLED_PROXY_ADDRESS,
)
from tests.support.compose import REPO, compose_file, fixed_address

COMPOSE: dict[str, Any] = compose_file()
SERVICES: dict[str, Any] = COMPOSE["services"]
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


FIXED = (("proxy", "edge"), ("web", "edge"), ("idp", "edge"), ("idp-db", "idp-store"))
"""Every container another one reaches on the host, and the network it is reached on."""


def _ipv4_pool(network: str) -> dict[str, str]:
    """Return the IPv4 pool of one network of ``docker-compose.yml``."""
    pools = COMPOSE["networks"][network]["ipam"]["config"]
    return next(pool for pool in pools if ipaddress.ip_network(pool["subnet"]).version == 4)


def test_every_hop_on_the_host_has_a_fixed_address_no_container_is_handed_adr_0125() -> None:
    """Outside the dynamic range and the gateway: while its owner is down, the address is free."""
    taken: dict[ipaddress.IPv4Address, str] = {}
    for service, network in FIXED:
        address, pool = fixed_address(service, network), _ipv4_pool(network)
        subnet = ipaddress.IPv4Network(pool["subnet"])

        assert SERVICES[service]["networks"][network] == {"ipv4_address": str(address)}
        assert address in subnet
        assert address not in ipaddress.IPv4Network(pool["ip_range"])
        assert address != subnet.network_address + 1  # Docker's gateway
        assert address not in taken, f"{service} and {taken.get(address)} share {address}"
        taken[address] = service
    check = compose_file("deploy/proxy/compose.pebble.yml")
    pebble = check["services"]["pebble"]["networks"]["edge"]["ipv4_address"]
    assert ipaddress.IPv4Address(pebble) not in taken  # scripts/check-proxy.sh's CA


def test_the_hops_name_those_addresses_wherever_they_are_written_adr_0125() -> None:
    """The compose file, the installer, the render script, the proxy and the start-up check."""
    web, idp = fixed_address("web", "edge"), fixed_address("idp", "edge")
    database = fixed_address("idp-db", "idp-store")
    install = (REPO / "scripts/install.sh").read_text()
    render = (REPO / "scripts/render-keycloak-realm.sh").read_text()
    caddyfile = (REPO / "deploy/proxy/Caddyfile").read_text()

    assert SERVICES["proxy"]["environment"]["WEB_UPSTREAM"] == f"{web}:8000"
    assert "reverse_proxy {$WEB_UPSTREAM}" in caddyfile
    assert not re.search(r"reverse_proxy [A-Za-z]", caddyfile)  # no route to a name
    assert BUNDLED_IDP_ADDRESS == str(idp)
    assert BUNDLED_IDP_UPSTREAM == f"{idp}:8080"
    assert re.search(rf"^BUNDLED_IDP_UPSTREAM={re.escape(BUNDLED_IDP_UPSTREAM)}$", install, re.M)
    assert re.search(rf"^IDP_ADDRESS={re.escape(str(idp))}$", render, re.MULTILINE)
    assert re.search(rf"^IDP_DB_ADDRESS={re.escape(str(database))}$", render, re.MULTILINE)
    assert not re.search(r"//idp:|idp-db:|IDP_UPSTREAM=idp", install + render)


def test_web_worker_and_the_proxy_share_the_network_of_the_provider_s_address() -> None:
    """The worker reads the staff directory, so it joins edge too (with a dynamic address)."""
    for service in ("proxy", "web", "worker", "idp"):
        assert "edge" in SERVICES[service]["networks"], service


def test_the_provider_s_database_is_alone_with_it_on_an_internal_network() -> None:
    """Neither the proxy, web nor worker reaches the database, and it has no way out."""
    members = {
        name for name, service in SERVICES.items() if "idp-store" in (service.get("networks") or {})
    }

    assert COMPOSE["networks"]["idp-store"]["internal"] is True
    assert members == {"idp", "idp-db"}
    assert set(SERVICES["idp-db"]["networks"]) == {"idp-store"}
