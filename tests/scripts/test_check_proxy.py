"""``scripts/check-proxy.sh`` asserts the standards of ADR 0086 in every pass.

The script needs Docker and runs in CI (step *proxy check*). This test keeps its shape honest
without Docker: the TLS-version and HSTS checks belong to ``edge_checks``, which every pass
calls, every pass also runs the round trip, the wrong proxy address is tried once, and the first
pass checks that a visitor over IPv6 keeps their address. Pass 3 and pass 5 (ADR 0126) put a
probe that answers like a hostile provider and then the pinned Keycloak behind the /idp route;
pass 4 (ADR 0125 step 2) stops each container another one reaches and lets an impostor answer its
name: these tests hold that the checks use the addresses a host gets and that each refusal
follows a control.
"""

import re
from pathlib import Path

import pytest

from app.api.security_headers import SECURITY_HEADERS
from tests.support.compose import compose_file
from tests.support.idp_route import dropped_headers, set_headers

REPO = Path(__file__).parents[2]
SCRIPT = (REPO / "scripts/check-proxy.sh").read_text()
FORUM_MINIMUM_HSTS_SECONDS = 31536000
"""One year, the Forum Standaardisatie minimum the script asserts."""


def _program(name: str) -> str:
    """Return the Python program the script keeps in the shell variable ``NAME_code``."""
    match = re.search(rf"^{name}_code='(.*?)'$", SCRIPT, re.MULTILINE | re.DOTALL)
    assert match, f"{name}_code is not in check-proxy.sh"
    return match.group(1)


@pytest.mark.parametrize(
    "name",
    ["sniffer", "ipv6_client", "idp_probe", "probe_direct", "directory", "responder", "fetch"],
)
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


def test_pass_three_puts_a_hostile_provider_behind_the_route() -> None:
    """ADR 0126: a probe sends what the route must drop; first asked directly, as the control."""
    probe = _program("idp_probe")
    body = _body("idp_route_checks")

    # Every header the route drops or sets, the probe sends, and a header the route sets comes
    # from the probe with another value, or its value on the wire would prove nothing. At run time
    # the probe is asked directly for the dropped ones before the proxy is judged.
    for name in dropped_headers():
        assert f'("{name}", ' in probe, f"the probe does not send {name}"
    for name, value in set_headers().items():
        assert f'("{name}", ' in probe, f"the probe does not send {name}"
        assert f'("{name}", "{value}")' not in probe, f"the probe sends the route's own {name}"
    assert body.index("so its absence would prove nothing") < body.index("the proxy passes $header")
    assert '"__Host-op_admin=forged; Path=/;' in probe  # would replace the portal's session
    # HSTS and the permissions on /idp, and every header of the proxy's own refusals there, must
    # equal what the same proxy returns for the portal itself.
    assert body.index('"$base/healthz"') < body.index("portal_hsts=")
    assert body.index('"$base/healthz"') < body.index("portal_permissions=")
    assert body.index('"$base/healthz"') < body.index("portal_security=")
    names = re.search(r"^security_names='([^']*)'$", SCRIPT, re.MULTILINE)
    assert names, "the script names no security headers"
    assert names.group(1).split() == [name.decode() for name in SECURITY_HEADERS]
    assert "the proxy passes the detour" in body
    assert body.index("the proxy passes the detour") < body.index("a detour reached the provider")
    assert re.search(
        r"^start_idp_probe\nexport PROXY_INTERNAL=true PROXY_IDP_UPSTREAM=idp-probe:8080$",
        SCRIPT,
        re.M,
    )
    assert re.search(r"^idp_route_checks$", SCRIPT, re.MULTILINE)


def test_pass_five_runs_the_pinned_keycloak_as_compose_defines_it() -> None:
    """The overlay changes the realm, the data and the port; image and command stay compose's,
    and the real Keycloak takes the place and the address of pass 4's stand-in."""
    overlay = (REPO / "deploy/proxy/compose.pebble.yml").read_text()
    idp = overlay[overlay.index("\n  idp:\n") :]
    text = _pass(5)

    assert "image:" not in idp
    assert "command:" not in idp
    assert "${KEYCLOAK_CHECK_DIR:?set by scripts/check-proxy.sh}/secrets/keycloak-realm.json" in idp
    assert '"127.0.0.1:18090:8080"' in idp
    assert re.search(r'^standins=""\nkeycloak_up\nkeycloak_checks$', text, re.MULTILINE)
    assert "--no-deps idp" in _body("keycloak_up")  # pass 4's stand-in idp-db stays untouched
    # The route stays where pass 4 put it: the address the installer writes, never a name.
    assert 'export PROXY_IDP_UPSTREAM="$BUNDLED_IDP_ADDRESS:$BUNDLED_IDP_PORT"' in _pass(4)
    assert "PROXY_IDP_UPSTREAM" not in text
    assert "--profile idp" in _body("compose")


def test_the_stand_ins_are_layered_in_pass_4_only() -> None:
    """Pass 5 must meet the Keycloak of docker-compose.yml, not the stand-in of pass 4."""
    assert "${standins:+-f $standins}" in _body("compose")
    assigned = re.findall(r"^standins=(\S*)", SCRIPT, re.MULTILINE)
    assert assigned == ['""', "deploy/proxy/compose.standins.yml", '""']
    assert "standins=deploy/proxy/compose.standins.yml" in _pass(4)


def test_pass_five_proves_each_refusal_against_the_loopback_and_the_console_after_the_change() -> (
    None
):
    body = _body("keycloak_checks")

    # A 404 through the route counts only when the loopback serves the same path.
    assert "so its 404 proves nothing" in body
    # Before the change the console's login is the public address's: the control for the change,
    # which runs the installer's and the runbook's own helper.
    change = "compose exec -T idp sh /opt/openplanner/kcadm-as-admin.sh update realms/master"
    assert body.index("did not name the public address before the change") < body.index(change)
    assert body.index(change) < body.index("does not stay on the loopback")
    # The helper itself still logs in once realm master's frontend URL is the loopback.
    assert body.index("does not stay on the loopback") < body.index(
        "kcadm.sh no longer works once realm master's frontend URL is the loopback"
    )
    assert 'assets_load "$page" "the login page"' in body
    assert "the account console" in body
    # The staff directory as web builds it, from the values the render script wrote for web.
    directory = _program("directory")
    assert "directory_from_settings(Settings())" in directory
    assert ".reachable()" in directory
    assert "secrets/keycloak-app.env" in body


def _pass(number: int) -> str:
    """Return pass NUMBER of the script, from its heading to its closing line."""
    start = SCRIPT.index(f"# Pass {number}:")
    return SCRIPT[start : SCRIPT.index(f'echo "pass {number} ok', start)]


def test_pass_4_proves_the_addresses_a_host_gets_not_addresses_of_its_own() -> None:
    """The login, directory and database host from the render script, the /idp route from the
    address file the installer reads too."""
    text = _pass(4)

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
    starts = re.split(r"^(?=impostor )", _pass(4), flags=re.MULTILINE)[1:]
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
    check = compose_file("deploy/proxy/compose.standins.yml")["services"]
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

    assert cleanup.index('"$project-impostor-$name"') < cleanup.index("compose down -v")
    assert cleanup.index('"$project-idp-probe"') < cleanup.index("compose down -v")


def test_an_impostor_counts_only_the_hops_that_reach_it() -> None:
    """The image's health check asks 127.0.0.1:8000, which the impostor for web answers."""
    assert "--no-healthcheck" in _body("impostor")
