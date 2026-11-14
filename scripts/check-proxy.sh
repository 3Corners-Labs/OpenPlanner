#!/usr/bin/env sh
# Start the compose stack with --profile tls against Pebble, Let's Encrypt's test ACME CA, and
# check what ADR 0030, ADR 0064 and ADR 0070 promise: a certificate is obtained without manual
# steps, port 80 redirects browser paths and refuses machine paths (/api, /webhooks) with 403,
# the proxy passes only what the app should see (TRACE, /static/, no-store on HTML), public form bodies are capped, the proxy runs as non-root; the
# standards of ADR 0086 hold (TLS 1.2 and 1.3 only with AEAD suites, HSTS of at least a year and
# none on port 80, and the app's round trip through the proxy is `ok`, or `proxy_not_trusted` with
# a wrong FORWARDED_ALLOW_IPS); a forged X-Forwarded-For/-Proto/-Host from the visitor is replaced
# by the proxy before it reaches web (ASVS V4.1.3, V15.3.4; read from the wire); a visitor over
# IPv6 reaches web with their own address, not one the whole of IPv6 shares (the dual-stack edge
# network of docker-compose.yml); a
# pair handed over through the portal-certs volume is served within the poll interval and ACME
# applies again when it is withdrawn; then the same proxy serves an own certificate from files and
# one issued by its internal authority (ADR 0112; the root is published, trusted by `tls-check`),
# and `tls-check` from the web container reads either certificate and judges it. The /idp route
# of ADR 0126 is proven twice: against a probe that answers like a hostile provider (only the
# login passes, the portal's cookies stay out, nothing in an answer acts on the whole origin) and
# against the Keycloak image docker-compose.yml pins (what a browser needs loads, the admin console
# and realm master do not, the console works on the loopback, the staff directory's calls work).
# Used by CI and by `make proxy-check`. Needs docker with compose v2, curl and openssl.
set -eu

project=openplanner-proxy-check
certs="$(mktemp -d)"
keycloak_dir="$certs/keycloak" # the realm of pass 4, rendered for this check's address
# A visitor over IPv6 (ipv6_client_check): the proxy is also published on the gateway address of
# a probe network made below, without masquerading, so a request from that address keeps it.
probe6_network=$project-ipv6
probe6_subnet=fdf2:d3f5:96ab:1::/64
probe6=fdf2:d3f5:96ab:1::1
compose() {
  PEBBLE_CERTS="$certs" PROBE6_ADDRESS="$probe6" KEYCLOAK_CHECK_DIR="$keycloak_dir" docker compose \
    -p "$project" -f docker-compose.yml -f deploy/proxy/compose.pebble.yml --profile tls --profile idp "$@"
}
cleanup() {
  docker rm -f "$project-idp-probe" >/dev/null 2>&1 || true
  compose down -v >/dev/null 2>&1 || true
  docker network rm "$probe6_network" >/dev/null 2>&1 || true
  rm -rf "$certs"
}
trap cleanup EXIT
docker network rm "$probe6_network" >/dev/null 2>&1 || true # left by an interrupted run
docker network create --ipv6 --subnet "$probe6_subnet" --gateway "$probe6" \
  -o com.docker.network.bridge.enable_ip_masquerade=false "$probe6_network" >/dev/null

# One source for the Pebble version: the compose overlay, which Renovate updates (ADR 0051).
pebble_image="$(sed -n 's|^ *image: *\(ghcr.io/letsencrypt/pebble:[^ ]*\)$|\1|p' deploy/proxy/compose.pebble.yml)"
[ -n "$pebble_image" ] || { echo "no Pebble image in deploy/proxy/compose.pebble.yml" >&2; exit 1; }
pebble_container="$(docker create "$pebble_image")"
docker cp "$pebble_container:/test/certs/pebble.minica.pem" "$certs/"
docker rm "$pebble_container" >/dev/null
# The proxy runs as uid 10001: mktemp -d makes a 0700 directory it could not enter on Linux.
chmod 755 "$certs"
chmod 644 "$certs/pebble.minica.pem"

resolve="--resolve booking.test:18443:127.0.0.1"
fail() { echo "proxy check failed: $1" >&2; compose logs proxy >&2; exit 1; }

wait_for_proxy() {
  for _ in $(seq 1 30); do
    # shellcheck disable=SC2086
    if curl -sk $resolve -o /dev/null https://booking.test:18443/healthz; then return 0; fi
    sleep 2
  done
  fail "the proxy did not answer on 18443"
}

issuer() { # issuer: the issuer line of the served certificate
  # shellcheck disable=SC2086
  curl -skv $resolve -o /dev/null https://booking.test:18443/healthz 2>&1 | grep 'issuer:' || true
}

oversized_post() { # oversized_post: the status of a 100 kB form post, 413 when the proxy refuses it
  # The proxy answers 413 once it has read more than its limit (64 kB). The app does not read
  # the body of a post to /g/x and answers 405 at once, so when that answer reaches the proxy
  # before the proxy has read the limit, the client sees the 405: a race, not a missing limit.
  # Without the limit every attempt would see 405, so one 413 in ten attempts proves it.
  status=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    # shellcheck disable=SC2086
    status="$(head -c 100000 /dev/zero | curl -sk $resolve -o /dev/null -w '%{http_code}' -X POST --data-binary @- https://booking.test:18443/g/x)"
    [ "$status" = "413" ] && break
  done
  printf '%s' "$status"
}

status_of() { # status_of CURL_ARGS...: the HTTP status code of a request through the proxy
  # shellcheck disable=SC2086
  curl -sk $resolve -o /dev/null -w '%{http_code}' "$@"
}

machine_paths_check() { # port 80 refuses machine paths without a Location header (ASVS V4.1.2)
  for path in /api/agent/v1/oauth/token /api/v1/widget/x /webhooks/x; do
    for method in GET POST; do
      head="$(curl -s -D - -o /dev/null -X "$method" -H 'Host: booking.test' "http://127.0.0.1:18080$path")"
      echo "$head" | head -n 1 | grep -q ' 403' || fail "port 80 $method $path is not refused with 403"
      ! echo "$head" | grep -qi '^location:' || fail "port 80 $method $path answers with a Location header"
    done
  done
}

https_edge_checks() { # what the proxy lets through on 443: TRACE, static directory, caching of HTML
  [ "$(status_of -X TRACE https://booking.test:18443/g/x)" = "405" ] || fail "TRACE is not answered with 405"
  [ "$(status_of https://booking.test:18443/static/)" = "404" ] || fail "/static/ lists or serves a directory"
  for path in /b/x /admin/login; do # /admin itself only redirects; the login page is its HTML
    # shellcheck disable=SC2086
    curl -sk $resolve -D - -o /dev/null "https://booking.test:18443$path" | grep -qi '^cache-control:.*no-store' \
      || fail "$path is not marked Cache-Control: no-store"
  done
}

tls_negotiated() { # tls_negotiated OPENSSL_ARGS...: "TLSv1.3 <cipher>", or "(NONE) ..." when refused
  # `New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384` is printed by OpenSSL and LibreSSL alike; a
  # refused handshake prints `New, (NONE), Cipher is (NONE)`. SECLEVEL=0 lets the client offer
  # TLS 1.0/1.1 at all, so a refusal is the server's.
  openssl s_client -connect 127.0.0.1:18443 -servername booking.test -cipher 'ALL:@SECLEVEL=0' \
    "$@" </dev/null 2>&1 | sed -n 's/^New, \([^,]*\), Cipher is \(.*\)$/\1 \2/p' | head -n 1
}

tls_version_checks() { # NCSC TLS guidelines (ADR 0086): TLS 1.2 and 1.3 only, AEAD suites
  for flag in -tls1 -tls1_1; do
    openssl s_client -help 2>&1 | grep -q -- "$flag" \
      || fail "this openssl cannot offer $flag, so a refusal could not be told from a missing client"
    refused="$(tls_negotiated "$flag")"
    case "$refused" in "(NONE)"*|"") ;; *) fail "the proxy accepted $flag ($refused)" ;; esac
  done
  # The positive controls: the same client works on 1.2 and 1.3, so the refusals above are real.
  tls12="$(tls_negotiated -tls1_2)"
  tls13="$(tls_negotiated -tls1_3)"
  case "$tls12" in TLSv1.2*) ;; *) fail "TLS 1.2 is not accepted ($tls12)" ;; esac
  case "$tls13" in TLSv1.3*) ;; *) fail "TLS 1.3 is not accepted ($tls13)" ;; esac
  # TLS 1.3 suites are all AEAD; for 1.2 the negotiated suite must be GCM or ChaCha20-Poly1305.
  echo "$tls12" | grep -Eq 'GCM|CHACHA20' || fail "TLS 1.2 negotiated a suite that is not AEAD ($tls12)"
}

hsts_checks() { # Forum Standaardisatie, RFC 6797: HSTS of a year or more on HTTPS, never on HTTP
  # shellcheck disable=SC2086
  header="$(curl -sk $resolve -D - -o /dev/null https://booking.test:18443/healthz \
    | tr -d '\r' | grep -i '^strict-transport-security:' || true)"
  [ -n "$header" ] || fail "no Strict-Transport-Security header on a proxied response"
  max_age="$(echo "$header" | sed -n 's/.*[mM]ax-[aA]ge=\([0-9]*\).*/\1/p')"
  [ -n "$max_age" ] && [ "$max_age" -ge 31536000 ] || fail "HSTS max-age is below one year ($header)"
  echo "$header" | grep -qi 'includeSubDomains' || fail "HSTS lacks includeSubDomains ($header)"
  # RFC 6797 section 7.2: a host must not send the header over plain HTTP.
  ! curl -s -D - -o /dev/null -H 'Host: booking.test' 'http://127.0.0.1:18080/g/x' \
    | grep -qi '^strict-transport-security:' || fail "port 80 sends Strict-Transport-Security"
}

trust_root() { # trust_root SOURCE: put the CA that signs the served chain in web's /tmp/trusted-ca.pem
  # SOURCE "pebble": the root of the test CA, from Pebble's management API (its own TLS is the
  # minica); otherwise a certificate file in /pebble that is its own trust anchor (self-signed).
  compose exec -T web python -c "
import ssl, sys, urllib.request
source = sys.argv[1]
if source == 'pebble':
    context = ssl.create_default_context(cafile='/pebble/pebble.minica.pem')
    pem = urllib.request.urlopen('https://pebble:15000/roots/0', context=context).read()
else:
    pem = open('/pebble/' + source, 'rb').read()
open('/tmp/trusted-ca.pem', 'wb').write(pem)
" "$1"
}

round_trip() { # round_trip FINDING SOURCE: web asks the public address through the proxy (ADR 0086)
  trust_root "$2"
  set +e
  report="$(compose exec -T -e PUBLIC_BASE_URL=https://booking.test:8443 web \
    python -m app.cli address-check --ca-file /tmp/trusted-ca.pem)"
  code=$?
  set -e
  echo "$report" | sed 's/^/  address-check: /'
  echo "$report" | grep -Eq "^public +https://booking.test:8443 +$1( |\$)" \
    || fail "address-check did not find '$1'"
  if [ "$1" = "ok" ]; then
    [ "$code" = "0" ] || fail "address-check should exit 0 through the proxy (exit $code)"
    echo "$report" | grep -q 'scheme=https  forwarded=honoured' \
      || fail "the app did not see https with the forwarded headers honoured"
  else
    [ "$code" = "1" ] || fail "address-check should exit 1 on a finding (exit $code)"
  fi
}

# Reads the X-Forwarded-* lines that cross the web container's network interface, nothing else
# (no path, no cookie, no token is printed). Runs in web's own network namespace.
sniffer_code='
import socket, sys, time
raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(3))
raw.settimeout(1)
print("ready", flush=True)
end = time.time() + int(sys.argv[1])
while time.time() < end:
    try:
        packet = raw.recv(65535)
    except socket.timeout:
        continue
    for line in packet.split(b"\r\n"):
        if line.lower().startswith(b"x-forwarded-"):
            print(line.decode("latin-1"), flush=True)
'

on_the_wire() { # on_the_wire REQUEST: the X-Forwarded-* lines web is given while REQUEST runs
  # The only place that shows what web is given is the wire between proxy and web: a sniffer in
  # web's network namespace prints the X-Forwarded-* lines, so the app needs no hook and no
  # address is ever stored or logged.
  web="$(compose ps -q web)"
  image="$(docker inspect -f '{{.Config.Image}}' "$web")"
  sniffer="$(docker run -d --rm --network "container:$web" --cap-add NET_RAW --user 0 \
    --entrypoint python "$image" -I -c "$sniffer_code" 30)"
  for _ in $(seq 1 30); do
    docker logs "$sniffer" 2>&1 | grep -q '^ready$' && break
    sleep 1
  done
  "$1" >&2
  sleep 2
  docker logs "$sniffer" 2>&1 | tr -d '\r'
  docker stop "$sniffer" >/dev/null 2>&1 || true
}

forged_request() { # the visitor claims another address, scheme and host
  # shellcheck disable=SC2086
  curl -sk $resolve -o /dev/null -H 'X-Forwarded-For: 203.0.113.7' -H 'X-Forwarded-Proto: http' \
    -H 'X-Forwarded-Host: forged.example' https://booking.test:18443/healthz
}

forged_header_check() { # a forged X-Forwarded-* is replaced at the edge (ASVS V4.1.3, V15.3.4)
  # The visitor claims to be 203.0.113.7 (a documentation address, RFC 5737), to have used http
  # and to be asking for another host. Caddy has no trusted_proxies, so it must replace all
  # three; the sniffer shows what web is given.
  seen="$(on_the_wire forged_request)"
  echo "$seen" | sed 's/^/  on the wire to web: /'
  forwarded_for="$(echo "$seen" | grep -i '^x-forwarded-for:' || true)"
  [ -n "$forwarded_for" ] || fail "the sniffer saw no proxied request, so the forged header could not be judged"
  ! echo "$seen" | grep -q '203\.0\.113\.7' || fail "web receives the X-Forwarded-For the visitor forged"
  [ "$(echo "$forwarded_for" | wc -l)" = "1" ] || fail "web receives more than one X-Forwarded-For line: $forwarded_for"
  ! echo "$forwarded_for" | grep -q ',' \
    || fail "the proxy kept the visitor's value next to its own (not a single address): $forwarded_for"
  echo "$seen" | grep -qi '^x-forwarded-proto: https$' || fail "web does not receive X-Forwarded-Proto: https from the proxy"
  ! echo "$seen" | grep -qi 'forged.example' || fail "web receives the X-Forwarded-Host the visitor forged"
}

# One request to the proxy from the address given as the first argument, port 18444. Only the
# proxy's answer matters here, not its certificate.
ipv6_client_code='
import socket, ssl, sys
context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
with socket.create_connection((sys.argv[1], 18444), timeout=10) as raw:
    with context.wrap_socket(raw, server_hostname="booking.test") as tls:
        tls.sendall(b"GET /healthz HTTP/1.1\r\nHost: booking.test\r\nConnection: close\r\n\r\n")
        tls.recv(64)
'

ipv6_request() { # a request from the probe address, made in the host's network namespace
  # From a container on the host network, so that on Docker Desktop too the request comes from
  # the machine that runs Docker, where the probe network's gateway address lives.
  docker run --rm --network host --entrypoint python "$image" -I -c "$ipv6_client_code" "$probe6" \
    || echo "the request over IPv6 to [$probe6]:18444 failed"
}

ipv6_client_check() { # a visitor over IPv6 keeps their address (docker-compose.yml, network edge)
  # The request comes from the probe network's gateway address to the port published there.
  # Docker must forward it to the proxy with NAT, so the proxy sees that address and puts it in
  # X-Forwarded-For. Docker's userland proxy (an edge network without IPv6, ip6tables off, Docker
  # Engine before 27) opens a new connection from the edge bridge instead, and every visitor over
  # IPv6 would share that one address in the rate limits.
  seen="$(on_the_wire ipv6_request)"
  echo "$seen" | sed 's/^/  on the wire to web, a visitor over IPv6: /'
  forwarded_for="$(echo "$seen" | grep -i '^x-forwarded-for:' | sed 's/^[^:]*: *//' || true)"
  [ -n "$forwarded_for" ] || fail "no request over IPv6 reached web, so the visitor's address could not be judged"
  [ "$forwarded_for" = "$probe6" ] \
    || fail "a visitor over IPv6 at $probe6 reaches web as $forwarded_for: Docker's userland proxy hides the address (edge network without IPv6, or ip6tables off)"
}

edge_checks() { # redirect, machine paths, body limit, non-root, standards: the same in every pass
  redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H 'Host: booking.test' 'http://127.0.0.1:18080/g/x?a=1')"
  [ "$redirect" = "308 https://booking.test/g/x?a=1" ] || fail "port 80 does not redirect ($redirect)"
  machine_paths_check
  https_edge_checks
  status="$(oversized_post)"
  [ "$status" = "413" ] || fail "oversized form body not refused ($status)"
  [ "$(compose exec -T proxy id -u)" != "0" ] || fail "proxy runs as root"
  tls_version_checks
  hsts_checks
  forged_header_check
}

# The provider of pass 3 (ADR 0126): it answers every request with 200 and the Cookie header it was
# given, sets every header an answer must not get through to the browser, and prints each path it
# serves. It runs in web's image on the edge network as idp-probe:8080.
idp_probe_code='
import http.server, json
FORGED = [
    ("Set-Cookie", "__Host-op_admin=forged; Path=/; Path=/; Secure; HttpOnly"),
    ("Set-Cookie", "tossed=1; Path =/admin; Domain = booking.test"),
    ("Set-Cookie", "KC_PROBE=1; Path=/idp/realms/openplanner/; Secure"),
    ("Strict-Transport-Security", "max-age=0"),
    ("Permissions-Policy", "camera=*, microphone=*, display-capture=*"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Content-Security-Policy", "frame-ancestors https://booking.test"),
    ("Service-Worker-Allowed", "/"),
    ("Clear-Site-Data", json.dumps("cookies")),
    ("Alt-Svc", "h2=\":9443\""),
    ("NEL", json.dumps({"report_to": "probe", "max_age": 86400})),
    ("Report-To", json.dumps({"group": "probe", "max_age": 86400, "endpoints": [{"url": "https://probe.invalid/"}]})),
    ("Reporting-Endpoints", "probe=\"https://probe.invalid/\""),
    ("Accept-CH", "Sec-CH-UA-Model"),
    ("Critical-CH", "Sec-CH-UA-Model"),
]


class Probe(http.server.BaseHTTPRequestHandler):
    def answer(self):
        print(self.path, flush=True)
        body = json.dumps({"cookie": self.headers.get_all("Cookie") or []}).encode()
        self.send_response(200)
        for name, value in FORGED:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = answer

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("", 8080), Probe).serve_forever()
'

# Asks the probe directly, from web, which headers it sends: the control for their absence later.
probe_direct_code='
import urllib.request
answer = urllib.request.urlopen("http://idp-probe:8080/idp/realms/openplanner/x")
for name, value in answer.headers.items():
    print(name + ": " + value)
'

# The staff directory (ADR 0095) as web builds it: its own settings, given the values the render
# script wrote for web (secrets/keycloak-app.env), and its own adapter. Prints whether one listing
# works, nothing else.
directory_code='
import asyncio
from app.adapters.keycloak_directory import directory_from_settings
from app.config import Settings
directory = directory_from_settings(Settings())
print("reachable" if directory and asyncio.run(directory.reachable()) else "unreachable")
'

# The headers the route drops, read from where they are written (deploy/proxy/idp.caddy).
dropped_headers="$(sed -n 's/^[[:space:]]*header_down -\([^[:space:]]*\)$/\1/p' deploy/proxy/idp.caddy)"

start_idp_probe() { # the provider of pass 3, on the edge network as idp-probe:8080
  image="$(docker inspect -f '{{.Config.Image}}' "$(compose ps -q web)")"
  docker rm -f "$project-idp-probe" >/dev/null 2>&1 || true
  docker run -d --rm --name "$project-idp-probe" --network "${project}_edge" --network-alias idp-probe \
    --entrypoint python "$image" -I -c "$idp_probe_code" >/dev/null
}

values_of() { # values_of NAME: the values of one header of $head, a line each, whatever its case
  echo "$head" | grep -i "^$1:" | sed 's/^[^:]*: *//'
}

probe_saw() { # probe_saw PATTERN: whether a path matching the extended regex reached the probe
  docker logs "$project-idp-probe" 2>&1 | grep -E -q "$1"
}

idp_route_checks() { # pass 3: the route of ADR 0126 in front of a provider that tries everything
  base=https://booking.test:18443
  for path in /idp/realms/openplanner/protocol/openid-connect/auth /idp/resources/x/login.css; do
    [ "$(status_of "$base$path")" = "200" ] || fail "$path does not reach the provider"
  done
  # The probe answers 200 to anything, so a 404 is the proxy's own refusal.
  for path in /idp /idp/ /idp/admin/ /idp/admin/master/console/ /idp/admin/realms/openplanner/users \
    /idp/realms/master/ /idp/realms/master/protocol/openid-connect/token /idp/metrics /idp/health \
    /IDP/admin/ /idp/realms/openplanner/../../admin/ /idp/realms/openplanner/%2e%2e/%2e%2e/admin/; do
    [ "$(status_of --path-as-is "$base$path")" = "404" ] || fail "the proxy passes $path to the provider"
  done
  ! probe_saw '^/(idp|IDP)/?$|^/(idp|IDP)/(admin|realms/master|metrics|health)' \
    || fail "a path the proxy should refuse reached the provider"
  [ "$(status_of -H 'Service-Worker: script' "$base/idp/resources/sw.js")" = "403" ] \
    || fail "a service worker's script is fetched from the provider"
  ! probe_saw '^/idp/resources/sw\.js' || fail "the fetch of a service worker's script reached the provider"
  # shellcheck disable=SC2086
  given="$(curl -sk $resolve -H 'Cookie: __Host-op_admin=s1; KC_RESTART=k1; lang=nl; __Host-op_link=s2; AUTH_SESSION_ID=a1' \
    "$base/idp/realms/openplanner/x")"
  echo "  the provider was given: $given"
  for kept in KC_RESTART=k1 AUTH_SESSION_ID=a1; do
    echo "$given" | grep -q "$kept" || fail "the provider's own cookie $kept does not reach it ($given)"
  done
  ! echo "$given" | grep -q -e '__Host-' -e 'lang=' || fail "a cookie of the portal reaches the provider ($given)"
  direct="$(compose exec -T web python -I -c "$probe_direct_code" | tr -d '\r')"
  # shellcheck disable=SC2086
  head="$(curl -sk $resolve -D - -o /dev/null "$base/healthz" | tr -d '\r')"
  portal_hsts="$(values_of strict-transport-security)"
  portal_permissions="$(values_of permissions-policy)"
  [ -n "$portal_hsts" ] && [ -n "$portal_permissions" ] \
    || fail "the portal sends no HSTS or Permissions-Policy, so the ones on /idp have nothing to equal"
  # shellcheck disable=SC2086
  head="$(curl -sk $resolve -D - -o /dev/null "$base/idp/realms/openplanner/x" | tr -d '\r')"
  [ -n "$dropped_headers" ] || fail "no dropped header found in deploy/proxy/idp.caddy"
  for header in $dropped_headers; do
    echo "$direct" | grep -qi "^$header:" || fail "the probe sends no $header, so its absence would prove nothing"
    ! echo "$head" | grep -qi "^$header:" || fail "the proxy passes $header from the provider"
  done
  cookies="$(values_of set-cookie)"
  [ "$(echo "$cookies" | grep -c .)" = "3" ] || fail "the provider's three cookies do not all arrive ($cookies)"
  # Every Path attribute as a browser reads it (it uses the last), not only the first.
  outside="$(echo "$cookies" | tr ';' '\n' | sed -n 's/^[[:space:]]*[Pp][Aa][Tt][Hh][[:space:]]*=[[:space:]]*//p' \
    | grep -v -e '^/idp/' -e '^/idp[[:space:]]*$' || true)"
  [ -z "$outside" ] || fail "a cookie of the provider keeps a path outside /idp/ ($cookies)"
  ! echo "$cookies" | grep -qi 'domain *=' || fail "a cookie of the provider keeps its Domain ($cookies)"
  [ "$(values_of strict-transport-security)" = "$portal_hsts" ] || fail "HSTS on /idp is not the portal's own"
  [ "$(values_of permissions-policy)" = "$portal_permissions" ] \
    || fail "the Permissions-Policy on /idp is not the portal's own, so a page there may use the origin's camera"
  [ "$(values_of cross-origin-opener-policy)" = "same-origin-allow-popups" ] \
    || fail "the opener policy on /idp is not same-origin-allow-popups alone"
  values_of content-security-policy | grep -qx 'frame-ancestors https://booking.test' \
    || fail "the provider's own policy does not arrive next to the added one"
  values_of content-security-policy \
    | grep -qx "connect-src https://booking.test:\\*/idp/; form-action https://booking.test:\\*/idp/; worker-src 'none'" \
    || fail "the policy the proxy adds on /idp is missing"
}

keycloak_up() { # pass 4: the bundled provider of docker-compose.yml, with a realm for this check
  mkdir -p "$keycloak_dir/deploy/keycloak"
  cp deploy/keycloak/realm.template.json "$keycloak_dir/deploy/keycloak/"
  printf 'PUBLIC_BASE_URL=https://booking.test:18443\n' >"$keycloak_dir/.env"
  scripts/render-keycloak-realm.sh --dir "$keycloak_dir" >/dev/null 2>&1 || fail "the check's realm could not be rendered"
  compose up -d idp
  for _ in $(seq 1 60); do
    curl -sf -o /dev/null http://127.0.0.1:18090/idp/realms/openplanner/.well-known/openid-configuration && return 0
    sleep 2
  done
  compose logs idp >&2
  fail "the bundled identity provider did not start"
}

assets_load() { # assets_load HTML WHAT: every asset under /idp/ that a page names loads through the route
  assets="$(echo "$1" | grep -o -E '(href|src)="/idp/[^"]*"' | sed -E 's/^(href|src)="//; s/"$//' | sort -u)"
  [ -n "$assets" ] || fail "$2 names no asset under /idp/"
  for asset in $assets; do
    [ "$(status_of "https://booking.test:18443$asset")" = "200" ] || fail "$2 needs $asset, which does not load through the route"
  done
}

keycloak_checks() { # pass 4: what a browser needs passes the route, the admin console does not
  base=https://booking.test:18443
  loopback=http://127.0.0.1:18090
  pkce='response_type=code&scope=openid&state=s&nonce=n&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256'
  # shellcheck disable=SC2086
  issuer="$(curl -sk $resolve "$base/idp/realms/openplanner/.well-known/openid-configuration" \
    | sed -n 's/.*"issuer":"\([^"]*\)".*/\1/p')"
  [ "$issuer" = "$base/idp/realms/openplanner" ] || fail "the login's discovery document does not pass the route ($issuer)"
  # shellcheck disable=SC2086
  page="$(curl -sk $resolve "$base/idp/realms/openplanner/protocol/openid-connect/auth?client_id=openplanner-admin&$pkce&redirect_uri=https%3A%2F%2Fbooking.test%3A18443%2Fadmin%2Fcallback")"
  echo "$page" | grep -q "action=\"$base/idp/realms/openplanner/login-actions/authenticate" \
    || fail "the login page does not pass the route"
  assets_load "$page" "the login page"
  # shellcheck disable=SC2086
  assets_load "$(curl -sk $resolve "$base/idp/realms/openplanner/account/")" "the account console"
  for path in /idp/admin/master/console/ /idp/realms/master/.well-known/openid-configuration; do
    [ "$(status_of "$base$path")" = "404" ] || fail "$path passes the route"
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$loopback$path")" = "200" ] \
      || fail "$path is not served on the loopback either, so its 404 proves nothing"
  done
  [ "$(status_of "$base/idp/admin/realms/openplanner/users")" = "404" ] || fail "the admin REST API passes the route"
  # Detours through a public path. The console would answer 200 and the REST API 401 (asking for
  # a token): either means the detour reached them.
  for path in '/idp/realms/openplanner/..;/..;/admin/master/console/' '/idp/resources/..;/admin/master/console/' \
    '/idp/realms/openplanner/%2e%2e/%2e%2e/admin/master/console/' \
    '/idp/realms/openplanner/..;/..;/admin/realms/openplanner/users' '/idp/resources/..;/admin/realms/openplanner/users' \
    '/idp/realms/openplanner/%2e%2e/%2e%2e/admin/realms/openplanner/users'; do
    status="$(status_of --path-as-is "$base$path")"
    case $status in 200 | 401 | 403) fail "$path reaches the admin console or its API through the route ($status)" ;; esac
  done
  # The console on the loopback (ADR 0126 point 2). Until realm master's frontend URL is the
  # loopback, Keycloak serves the console's login for the public address, where the route refuses
  # it; afterwards for the loopback. Set as the installer and the runbook set it.
  master="$loopback/idp/realms/master/protocol/openid-connect/auth?client_id=security-admin-console&$pkce&redirect_uri="
  curl -s "${master}https%3A%2F%2Fbooking.test%3A18443%2Fidp%2Fadmin%2Fmaster%2Fconsole%2F" \
    | grep -q "action=\"$base/idp/realms/master/login-actions/" \
    || fail "realm master's login form did not name the public address before the change, so the next check proves nothing"
  compose exec -T idp sh /opt/openplanner/kcadm-as-admin.sh update realms/master \
    -s attributes.frontendUrl=http://127.0.0.1:18090/idp </dev/null \
    || fail "realm master did not take the loopback as its frontend URL"
  curl -s "${master}http%3A%2F%2F127.0.0.1%3A18090%2Fidp%2Fadmin%2Fmaster%2Fconsole%2F" \
    | grep -q "action=\"$loopback/idp/realms/master/login-actions/" \
    || fail "realm master's login form does not stay on the loopback"
  console="$(curl -s "$loopback/idp/admin/master/console/")"
  echo "$console" | grep -q "\"adminBaseUrl\": \"$loopback/idp\"" || fail "the console does not call its API on the loopback"
  ! echo "$console" | grep -q 'booking.test' || fail "the console on the loopback names the public address"
  app="$keycloak_dir/secrets/keycloak-app.env"
  [ "$(compose exec -T -e KEYCLOAK_ADMIN_URL="$(sed -n 's/^KEYCLOAK_ADMIN_URL=//p' "$app")" \
    -e KEYCLOAK_DIRECTORY_CLIENT_ID="$(sed -n 's/^KEYCLOAK_DIRECTORY_CLIENT_ID=//p' "$app")" \
    -e KEYCLOAK_DIRECTORY_CLIENT_SECRET="$(sed -n 's/^KEYCLOAK_DIRECTORY_CLIENT_SECRET=//p' "$app")" \
    web python -c "$directory_code" | tr -d '\r')" = "reachable" ] \
    || fail "the staff directory cannot use the provider on the internal network"
}

tls_check() { # tls_check EXPECTED_FINDING EXPECTED_ISSUER_FRAGMENT: run the CLI from web
  set +e
  report="$(compose exec -T web python -m app.cli tls-check --host booking.test --port 8443)"
  code=$?
  set -e
  echo "$report" | sed 's/^/  tls-check: /'
  [ "$code" = "1" ] || fail "tls-check should exit 1 on an untrusted certificate (exit $code)"
  echo "$report" | grep -q "^finding  *$1" || fail "tls-check did not find '$1'"
  echo "$report" | grep -q "^issuer  *.*$2" || fail "tls-check did not read the issuer '$2'"
  echo "$report" | grep -Eq '^valid until .*\([0-9]+ days\)' || fail "tls-check did not report the days left"
}

# Pass 1: ACME against Pebble.
compose up -d --build --wait proxy
wait_for_proxy
issuer | grep -q 'Pebble' || fail "no certificate from the ACME CA ($(issuer))"
edge_checks
ipv6_client_check
round_trip ok pebble
tls_check untrusted Pebble
echo "pass 1 ok: certificate obtained over ACME, redirect, body limit, non-root, TLS versions, HSTS, a visitor's IPv6 address, round trip, tls-check"

# A proxy address the app does not trust: the app must say so, not report ok (ADR 0086 point 2).
# In a subshell: an assignment in front of a shell function would outlive the call.
(CHECK_FORWARDED_ALLOW_IPS=10.255.255.1 && export CHECK_FORWARDED_ALLOW_IPS && compose up -d --force-recreate --wait web)
round_trip proxy_not_trusted pebble
compose up -d --force-recreate --wait web
round_trip ok pebble
echo "pass 1a ok: a wrong FORWARDED_ALLOW_IPS is found as proxy_not_trusted, the right one as ok"

self_signed() { # self_signed NAME ORGANISATION: a 30-day EC pair for booking.test in $certs
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 30 \
    -subj "/O=$2/CN=booking.test" -addext "subjectAltName=DNS:booking.test" \
    -keyout "$certs/$1.key" -out "$certs/$1.crt" >/dev/null 2>&1
}

wait_for_issuer() { # wait_for_issuer FRAGMENT: the watcher polls every 10 s (ADR 0070)
  for _ in $(seq 1 20); do
    if issuer | grep -q "$1"; then return 0; fi
    sleep 2
  done
  fail "the proxy did not switch to '$1' ($(issuer))"
}

# Pass 1b: the own certificate managed in the portal reaches the proxy through the
# portal-certs volume (ADR 0070): web keeps the key there and adds fullchain.pem and serial.
self_signed portal "OpenPlanner portal check"
# Through web's own adapter, as the portal does it: the volume belongs to uid 10001 and the key is
# written mode 600 by that user (`docker cp` would leave root-owned files the app cannot manage).
portal_volume() { # portal_volume PYTHON_STATEMENTS: run them with a FileCertificateVolume as `volume`
  compose exec -T -e KEY="$(cat "$certs/portal.key")" -e CHAIN="$(cat "$certs/portal.crt")" web python -c "
import os
from pathlib import Path
from app.adapters.certificate_volume import FileCertificateVolume
volume = FileCertificateVolume(Path('/srv/portal-certs'))
$1"
}
portal_volume "volume.import_key(os.environ['KEY'] + chr(10)); volume.publish(os.environ['CHAIN'] + chr(10), 'portal-1')"
wait_for_issuer 'OpenPlanner portal check'
edge_checks
round_trip ok portal.crt
tls_check untrusted "OpenPlanner portal check"
portal_volume "volume.withdraw()"
wait_for_issuer 'Pebble'
echo "pass 1b ok: portal certificate served from the volume and withdrawn again, tls-check"

# Pass 2: an own certificate served from files (ADR 0064), mounted at /pebble like the CA.
self_signed own "OpenPlanner own CA check"
chmod 644 "$certs/own.key" "$certs/own.crt" # the proxy's uid must read them (test only)
export PROXY_CERT_FILE=/pebble/own.crt PROXY_KEY_FILE=/pebble/own.key
compose up -d --force-recreate --wait proxy
wait_for_proxy
issuer | grep -q 'OpenPlanner own CA check' || fail "the own certificate is not served ($(issuer))"
edge_checks
round_trip ok own.crt
tls_check untrusted "OpenPlanner own CA check"
echo "pass 2 ok: own certificate from files, redirect, body limit, non-root, TLS versions, HSTS, round trip, tls-check"

# Pass 3: Caddy's internal authority (ADR 0112). No ACME, no mailbox; the public root is
# published to the internal-ca volume and the app's checks trust it. The /idp route of ADR 0126
# leads to a probe that answers like a hostile provider.
unset PROXY_CERT_FILE PROXY_KEY_FILE
start_idp_probe
export PROXY_INTERNAL=true PROXY_IDP_UPSTREAM=idp-probe:8080
compose up -d --force-recreate --wait proxy
wait_for_proxy
issuer | grep -q 'Caddy Local Authority' || fail "the internal authority did not issue the certificate ($(issuer))"
for _ in $(seq 1 15); do
  compose exec -T proxy test -s /internal-ca/root.crt && break
  sleep 2
done
compose exec -T proxy test -s /internal-ca/root.crt || fail "the root certificate was not published"
compose exec -T proxy sh -c 'grep -q "BEGIN CERTIFICATE" /internal-ca/root.crt' || fail "the published root is no certificate"
compose exec -T proxy sh -c 'grep -q "PRIVATE KEY" /internal-ca/root.crt' && fail "the published root holds a private key"
report="$(compose exec -T web python -m app.cli tls-check --host booking.test --port 8443)" \
  || fail "tls-check does not trust the internal root ($report)"
echo "$report" | sed 's/^/  tls-check: /'
echo "$report" | grep -q "^finding  *ok" || fail "tls-check did not find 'ok' for the internal certificate"
idp_route_checks
# shellcheck disable=SC2086
[ "$(curl -sk $resolve -o /dev/null -w '%{http_code}' https://booking.test:18443/healthz)" = "200" ] \
  || fail "the app is not served next to /idp/"
edge_checks
docker rm -f "$project-idp-probe" >/dev/null
echo "pass 3 ok: internal authority, root published without a key, tls-check ok, /idp passes only the login and keeps the portal's cookies and the whole origin out of the provider's reach, redirect, body limit, non-root, TLS versions, HSTS"

# Pass 4: the bundled identity provider itself behind the route (ADR 0126), the Keycloak image and
# service docker-compose.yml define. A Keycloak update (Renovate) runs it too.
keycloak_up
export PROXY_IDP_UPSTREAM=idp:8080
compose up -d --force-recreate --wait proxy
wait_for_proxy
keycloak_checks
compose stop idp >/dev/null 2>&1
echo "pass 4 ok: the bundled Keycloak's login, assets and account console pass the route; its admin console, REST API and realm master do not; the console works on the loopback; the staff directory works on the internal network"
unset PROXY_INTERNAL PROXY_IDP_UPSTREAM

# A half-configured own certificate must stop the container, never serve plain HTTP.
export PROXY_CERT_FILE=/pebble/own.crt
unset PROXY_KEY_FILE
compose up -d --force-recreate proxy >/dev/null 2>&1 || true
sleep 3
compose logs proxy 2>&1 | grep -q 'TLS_CERT_FILE and TLS_KEY_FILE go together' \
  || fail "a certificate without its key did not stop the proxy"
echo "proxy check passed"
