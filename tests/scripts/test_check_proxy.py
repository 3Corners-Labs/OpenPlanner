"""``scripts/check-proxy.sh`` asserts the standards of ADR 0086 in every pass.

The script needs Docker and runs in CI (step *proxy check*). This test keeps its shape honest
without Docker: the TLS-version and HSTS checks belong to ``edge_checks``, which every pass
calls, every pass also runs the round trip, the wrong proxy address is tried once, and the first
pass checks that a visitor over IPv6 keeps their address. Pass 4 (ADR 0125 step 2) stops each
container another one reaches and lets an impostor answer its name: these tests hold that it
uses the addresses a host gets and that each refusal follows a control.
"""

import re
from pathlib import Path

import pytest

from app.api.security_headers import SECURITY_HEADERS
from tests.support.compose import compose_file

REPO = Path(__file__).parents[2]
SCRIPT = (REPO / "scripts/check-proxy.sh").read_text()
FORUM_MINIMUM_HSTS_SECONDS = 31536000
"""One year, the Forum Standaardisatie minimum the script asserts."""


def _program(name: str) -> str:
    """Return the Python program the script keeps in the shell variable ``NAME_code``."""
    match = re.search(rf"^{name}_code='(.*?)'$", SCRIPT, re.MULTILINE | re.DOTALL)
    assert match, f"{name}_code is not in check-proxy.sh"
    return match.group(1)


@pytest.mark.parametrize("name", ["sniffer", "ipv6_client", "responder", "fetch"])
def test_every_program_the_script_runs_in_a_container_compiles(name: str) -> None:
    compile(_program(name), name, "exec")


def _body(name: str) -> str:
    """Return the body of the shell function ``name``."""
    match = re.search(rf"^{name}\(\) \{{.*?^\}}$", SCRIPT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} is not defined in check-proxy.sh"
    return match.group(0)


def test_every_pass_runs_the_standards_through_edge_checks() -> None:
    edge = _body("edge_checks")

    assert "tls_version_checks" in edge
    assert "hsts_checks" in edge
    passes = len(re.findall(r"^edge_checks$", SCRIPT, re.MULTILINE))
    assert passes == 4  # ACME, the portal's certificate, own files, the internal authority


def test_every_pass_asks_for_the_round_trip_through_the_proxy() -> None:
    rounds = re.findall(r"^round_trip ok \S+$", SCRIPT, re.MULTILINE)

    assert len(rounds) >= 4  # three passes, and the right address again after the wrong one
    assert re.search(r"^round_trip proxy_not_trusted \S+$", SCRIPT, re.MULTILINE)
    assert "FORWARDED_ALLOW_IPS" in SCRIPT


def test_the_tls_check_refuses_old_versions_and_needs_a_positive_control() -> None:
    body = _body("tls_version_checks")

    assert "-tls1 -tls1_1" in body
    assert "-tls1_2" in body
    assert "-tls1_3" in body
    assert "GCM|CHACHA20" in body


def test_the_hsts_floor_in_the_script_is_met_by_what_the_app_sends() -> None:
    body = _body("hsts_checks")
    sent = SECURITY_HEADERS[b"strict-transport-security"].decode()
    max_age = int(re.search(r"max-age=(\d+)", sent).group(1))  # type: ignore[union-attr]

    assert str(FORUM_MINIMUM_HSTS_SECONDS) in body
    assert max_age >= FORUM_MINIMUM_HSTS_SECONDS
    assert "includeSubDomains" in sent
    assert "includeSubDomains" in body


def test_every_pass_sends_a_forged_forwarded_header_and_looks_at_the_wire() -> None:
    """V4.1.3, V15.3.4: what the visitor forges must not be what web receives from the proxy."""
    assert "forged_header_check" in _body("edge_checks")
    body = _body("forged_header_check")
    request = _body("forged_request")
    wire = _body("on_the_wire")

    assert "on_the_wire forged_request" in body
    assert "X-Forwarded-For: 203.0.113.7" in request  # a documentation address (RFC 5737)
    assert "X-Forwarded-Proto: http" in request
    assert "--network" in wire  # the sniffer joins web's network namespace
    assert "NET_RAW" in wire
    # A control first: no header on the wire would also pass a "not forged" test.
    assert body.index("saw no proxied request") < body.index(
        "receives the X-Forwarded-For the visitor forged"
    )
    assert "single address" in body  # Caddy replaces; it does not append a second hop


def test_the_sniffer_prints_only_the_forwarded_headers() -> None:
    """The wire is read for ``X-Forwarded-*`` lines and nothing else (no paths, no tokens)."""
    code = _program("sniffer")

    assert 'startswith(b"x-forwarded-")' in code
    assert "print(line" in code


def test_a_visitor_over_ipv6_must_reach_web_with_their_own_address() -> None:
    """The dual-stack edge network: Docker's NAT keeps the address, its userland proxy does not."""
    body = _body("ipv6_client_check")

    assert "on_the_wire ipv6_request" in body
    assert re.search(r"^edge_checks\nipv6_client_check$", SCRIPT, re.MULTILINE)  # in pass 1
    # A control first: no request on the wire would also leave no shared address to find.
    assert body.index("no request over IPv6 reached web") < body.index(
        '[ "$forwarded_for" = "$probe6" ]'
    )
    # With masquerading, every request would come from the edge bridge, as through the userland
    # proxy, and the check could not tell the two apart.
    assert "enable_ip_masquerade=false" in SCRIPT
    overlay = (REPO / "deploy/proxy/compose.pebble.yml").read_text()
    assert '"[${PROBE6_ADDRESS:?set by scripts/check-proxy.sh}]:18444:8443"' in overlay


def _pass_4() -> str:
    """Return pass 4 of the script, from its heading to its closing line (ADR 0125 step 2)."""
    start = SCRIPT.index("# Pass 4:")
    return SCRIPT[start : SCRIPT.index('echo "pass 4 ok', start)]


def test_pass_4_proves_the_addresses_a_host_gets_not_addresses_of_its_own() -> None:
    """The login, directory and database host from the render script, the /idp route from the
    address file the installer reads too."""
    text = _pass_4()

    assert "bash scripts/render-keycloak-realm.sh" in text
    assert "--database postgres" in text
    for key in ("OIDC_DISCOVERY_URL", "KEYCLOAK_ADMIN_URL", "KC_DB_URL_HOST"):
        assert f"s/^{key}=//p" in text
    assert ". scripts/fixed-addresses.sh" in text
    assert '. "$DIR/scripts/fixed-addresses.sh"' in (REPO / "scripts/install.sh").read_text()
    assert "172.31.240." not in text
    # Every hop answers while its container runs: a refusal later is then the container's absence.
    for hop in ('fetch web "$login"', 'fetch worker "$directory"', 'fetch idp "$database"'):
        assert text.index(hop) < text.index("impostor idp-db idp-store 5432")


def test_an_impostor_is_reached_by_name_before_any_refusal_counts() -> None:
    """The control runs inside ``impostor``, so no refusal can come before it."""
    body = _body("impostor")

    assert body.index("[ $# -gt 0 ] || fail") < body.index('for client in "$@"')
    assert body.index('for client in "$@"') < body.index('requests "$impostor_name" >')
    assert 'fetch "$client" "$impostor_url"' in body
    assert "a refusal would prove nothing" in body


def _impostor_blocks() -> list[str]:
    """Return pass 4's blocks, each from its ``impostor`` line to the first blank line."""
    starts = re.split(r"^(?=impostor )", _pass_4(), flags=re.MULTILINE)[1:]
    return [start.split("\n\n", 1)[0] for start in starts]


def test_every_impostor_block_ends_with_untouched_for_the_same_name() -> None:
    """What the impostor answered after its controls is a hop that followed the name."""
    blocks = _impostor_blocks()

    assert [block.split()[1] for block in blocks] == ["idp-db", "idp", "web"]
    for block in blocks:
        name = block.split()[1]
        lines = block.strip().splitlines()
        assert lines[-1] == f"untouched {name}", block
        assert any('= "refused" ]' in line or '= " 502" ]' in line for line in lines[1:-1])


def test_every_client_that_must_fail_reached_the_impostor_by_name_first() -> None:
    """A refusal proves something only from a client that reached the impostor in its control."""
    for block in _impostor_blocks():
        head, *lines = block.splitlines()
        clients = set(head.split()[4:])  # impostor NAME NETWORK PORT CLIENT...
        refusing = {client for line in lines for client in re.findall(r"fetch (\w+) ", line)}
        if any("proxied " in line for line in lines):
            refusing.add("proxy")

        assert refusing, block
        assert refusing <= clients, block


def test_the_stand_ins_keep_where_docker_compose_puts_them() -> None:
    """The overlay swaps the image, never the networks or fixed addresses, and publishes nothing."""
    check = compose_file("deploy/proxy/compose.pebble.yml")["services"]
    base = compose_file()["services"]

    for name in ("idp", "idp-db"):
        assert "networks" not in check[name]
        assert check[name].get("ports", []) == []
        assert base[name]["networks"]
    assert "/pebble/responder.py" in check["idp"]["command"]


def test_the_responder_prints_one_line_per_request_and_nothing_it_carried() -> None:
    code = _program("responder")

    assert 'print("request", flush=True)' in code
    assert "def log_message" in code  # http.server's own access log, with paths, stays silent


def test_the_impostors_are_removed_before_the_networks_they_hold() -> None:
    cleanup = _body("cleanup")

    assert cleanup.index("impostor") < cleanup.index("compose down -v")


def test_an_impostor_counts_only_the_hops_that_reach_it() -> None:
    """The image's health check asks 127.0.0.1:8000, which the impostor for web answers."""
    assert "--no-healthcheck" in _body("impostor")
