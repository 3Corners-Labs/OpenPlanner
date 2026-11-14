"""``scripts/check-proxy.sh`` asserts the standards of ADR 0086 in every pass.

The script needs Docker and runs in CI (step *proxy check*). This test keeps its shape honest
without Docker: the TLS-version and HSTS checks belong to ``edge_checks``, which every pass
calls, every pass also runs the round trip, the wrong proxy address is tried once, and the first
pass checks that a visitor over IPv6 keeps their address.
"""

import re
from pathlib import Path

from app.api.security_headers import SECURITY_HEADERS

REPO = Path(__file__).parents[2]
SCRIPT = (REPO / "scripts/check-proxy.sh").read_text()
FORUM_MINIMUM_HSTS_SECONDS = 31536000
"""One year, the Forum Standaardisatie minimum the script asserts."""


def _body(name: str) -> str:
    """Return the body of the shell function ``name``."""
    match = re.search(rf"^{name}\(\) \{{.*?^\}}$", SCRIPT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} is not defined in check-proxy.sh"
    return match.group(0)


def _program(name: str) -> str:
    """Return the Python program the script keeps in the shell variable ``name``."""
    match = re.search(rf"^{name}='(.*?)'$", SCRIPT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} is not in the script"
    compile(match.group(1), name, "exec")
    return match.group(1)


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
    code = _program("sniffer_code")

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
    _program("ipv6_client_code")


def test_pass_three_puts_a_hostile_provider_behind_the_route() -> None:
    """ADR 0126: a probe sends what the route must drop; first asked directly, as the control."""
    probe = _program("idp_probe_code")
    body = _body("idp_route_checks")

    # Every header the route drops, rewrites or sets, the probe sends, and a header the route
    # sets comes from the probe with another value, or its value on the wire would prove nothing.
    # At run time the probe is asked directly for the dropped ones before the proxy is judged.
    route = (REPO / "deploy/proxy/idp.caddy").read_text()
    for name in set(re.findall(r"^\s*header_down [-+]?([A-Za-z-]+)\b", route, re.MULTILINE)):
        assert f'("{name}", ' in probe, f"the probe does not send {name}"
    set_by_route = re.findall(
        r'^\s*header_down \+?([A-Za-z-]+) (?:"([^"]*)"|([^\s"`]+))$', route, re.MULTILINE
    )
    assert len(set_by_route) == 4  # HSTS, Permissions-Policy, the opener policy, the added CSP
    for name, quoted, bare in set_by_route:
        assert f'("{name}", "{quoted or bare}")' not in probe, f"the probe sends the route's {name}"
    assert body.index("so its absence would prove nothing") < body.index("the proxy passes $header")
    assert '"__Host-op_admin=forged; Path=/;' in probe  # would replace the portal's session
    # HSTS and the permissions on /idp must equal what the same proxy returns for the portal.
    assert body.index('"$base/healthz"') < body.index("portal_hsts=")
    assert body.index('"$base/healthz"') < body.index("portal_permissions=")
    assert re.search(
        r"^start_idp_probe\nexport PROXY_INTERNAL=true PROXY_IDP_UPSTREAM=idp-probe:8080$",
        SCRIPT,
        re.M,
    )
    assert re.search(r"^idp_route_checks$", SCRIPT, re.MULTILINE)


def test_pass_four_runs_the_pinned_keycloak_as_compose_defines_it() -> None:
    """The overlay changes the realm, the data and the port; image and command stay compose's."""
    overlay = (REPO / "deploy/proxy/compose.pebble.yml").read_text()
    idp = overlay[overlay.index("\n  idp:\n") :]

    assert "image:" not in idp
    assert "command:" not in idp
    assert "${KEYCLOAK_CHECK_DIR:?set by scripts/check-proxy.sh}/secrets/keycloak-realm.json" in idp
    assert '"127.0.0.1:18090:8080"' in idp
    assert re.search(r"^keycloak_up\nexport PROXY_IDP_UPSTREAM=idp:8080$", SCRIPT, re.MULTILINE)
    assert re.search(r"^keycloak_checks$", SCRIPT, re.MULTILINE)
    assert "--profile idp" in _body("compose")


def test_pass_four_proves_each_refusal_against_the_loopback_and_the_console_after_the_change() -> (
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
    assert 'assets_load "$page" "the login page"' in body
    assert "the account console" in body
    # The staff directory as web builds it, from the values the render script wrote for web.
    directory = _program("directory_code")
    assert "directory_from_settings(Settings())" in directory
    assert ".reachable()" in directory
    assert "secrets/keycloak-app.env" in body
