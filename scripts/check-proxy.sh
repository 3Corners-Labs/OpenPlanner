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
# one issued by its internal authority (ADR 0112; the root is published, trusted by `tls-check`,
# and /idp/ is routed),
# and `tls-check` from the web container reads either certificate and judges it. Pass 4 shows
# that every hop on the host goes to a fixed address and fails closed while its container is down,
# even when an impostor answers the container's name as the host's DNS could (ADR 0125 step 2).
# Used by CI and by `make proxy-check`. Needs docker with compose v2, bash, curl and openssl.
set -eu

project=openplanner-proxy-check
certs="$(mktemp -d)"
# A visitor over IPv6 (ipv6_client_check): the proxy is also published on the gateway address of
# a probe network made below, without masquerading, so a request from that address keeps it.
probe6_network=$project-ipv6
probe6_subnet=fdf2:d3f5:96ab:1::/64
probe6=fdf2:d3f5:96ab:1::1
compose() {
  PEBBLE_CERTS="$certs" PROBE6_ADDRESS="$probe6" docker compose -p "$project" \
    -f docker-compose.yml -f deploy/proxy/compose.pebble.yml --profile tls "$@"
}
cleanup() {
  # The impostors of pass 4 first: a network with a container attached cannot be removed.
  for name in idp idp-db web; do docker rm -f "$project-impostor-$name" >/dev/null 2>&1 || true; done
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

# A stand-in or an impostor of pass 4: answers every request with its name and prints one line
# per request, never a path, a header or a body, so the script can count what reached it.
responder_code='
import http.server
import sys

name, port = sys.argv[1], int(sys.argv[2])


class Answer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = name.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print("request", flush=True)

    do_POST = do_GET

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", port), Answer).serve_forever()
'

# What a container gets from a URL: the body, or `refused` (and why, on stderr) when nothing
# answers within ten seconds.
fetch_code='
import sys
import urllib.request

try:
    print(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode())
except OSError as error:
    print("refused")
    print(f"  {sys.argv[1]}: {error}", file=sys.stderr)
'

fetch() { # fetch SERVICE URL: what SERVICE gets from URL, `refused` when nothing answers
  if [ "$1" = proxy ]; then # Caddy's image has no Python; busybox wget does the same
    compose exec -T proxy wget -q -O - -T 10 "$2" || echo refused
  else
    compose exec -T "$1" python -I -c "$fetch_code" "$2"
  fi
}

proxied() { # proxied PATH: the body of a request through the proxy, a space, its status
  # shellcheck disable=SC2086
  curl -sk $resolve -w ' %{http_code}' "https://booking.test:18443$1"
}

requests() { # requests NAME: how many requests the impostor for NAME has answered
  docker logs "$project-impostor-$1" 2>/dev/null | grep -c '^request$' || true
}

impostor() { # impostor NAME NETWORK PORT CLIENT...: stop container NAME, let another answer NAME
  # on NETWORK as this host's DNS could, and make each CLIENT reach it by that name first (the
  # control: with nobody answering, a refusal would prove nothing). What it answered so far is
  # kept for `untouched`. Without the image's own health check, which asks 127.0.0.1:8000.
  impostor_name=$1 impostor_url="http://$1:$3/"
  docker stop "$(compose ps -q "$1")" >/dev/null
  docker run -d --rm --no-healthcheck --name "$project-impostor-$1" --network "${project}_$2" \
    --network-alias "$1" -v "$certs:/pebble:ro" --entrypoint python "$image" \
    -I /pebble/responder.py "impostor $1" "$3" >/dev/null
  shift 3
  [ $# -gt 0 ] || fail "impostor $impostor_name names no client, so nothing would prove the name first"
  for client in "$@"; do
    tries=0
    until [ "$(fetch "$client" "$impostor_url" 2>/dev/null)" = "impostor $impostor_name" ]; do
      tries=$((tries + 1))
      [ "$tries" -lt 15 ] || fail "nothing answers $impostor_url for $client, so a refusal would prove nothing"
      sleep 1
    done
  done
  requests "$impostor_name" >"$certs/seen-$impostor_name"
}

untouched() { # untouched NAME: the impostor for NAME answered nothing since its controls
  [ "$(requests "$1")" = "$(cat "$certs/seen-$1")" ] || fail "the impostor for $1 received a hop meant for $1"
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
# published to the internal-ca volume, the app's checks trust it, and /idp/ is routed.
unset PROXY_CERT_FILE PROXY_KEY_FILE
export PROXY_INTERNAL=true PROXY_IDP_UPSTREAM=127.0.0.1:9
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
# shellcheck disable=SC2086
[ "$(curl -sk $resolve -o /dev/null -w '%{http_code}' https://booking.test:18443/idp/realms/x)" = "502" ] \
  || fail "/idp/ is not routed to IDP_UPSTREAM"
# shellcheck disable=SC2086
[ "$(curl -sk $resolve -o /dev/null -w '%{http_code}' https://booking.test:18443/healthz)" = "200" ] \
  || fail "the app is not served next to /idp/"
edge_checks
echo "pass 3 ok: internal authority, root published without a key, tls-check ok, /idp routed, redirect, body limit, non-root, TLS versions, HSTS"

# Pass 4: every hop on the host goes to a fixed address, never to a name (ADR 0125 step 2).
# Stand-ins answer at the addresses docker-compose.yml gives the bundled identity provider and its
# database (compose.pebble.yml). The login, the staff directory and the database host are what
# the render script writes, the /idp route what the installer writes (IDP_UPSTREAM), the route to
# web what docker-compose.yml hands the proxy (WEB_UPSTREAM). Then each container is stopped and
# an impostor answers its name on that network, as the host's DNS could: the name reaches the
# impostor (the control, so a refusal means something), the hop fails, and the impostor sees none
# of it.
printf '%s' "$responder_code" >"$certs/responder.py"
chmod 644 "$certs/responder.py"
render="$certs/render"
mkdir -p "$render/deploy/keycloak"
cp deploy/keycloak/realm.template.json "$render/deploy/keycloak/"
printf 'PUBLIC_BASE_URL=https://booking.test\n' >"$render/.env"
bash scripts/render-keycloak-realm.sh --dir "$render" --database postgres >/dev/null 2>&1 \
  || fail "the realm could not be rendered"
login="$(sed -n 's/^OIDC_DISCOVERY_URL=//p' "$render/secrets/keycloak-app.env")"
directory="$(sed -n 's/^KEYCLOAK_ADMIN_URL=//p' "$render/secrets/keycloak-app.env")"
database="http://$(sed -n 's/^KC_DB_URL_HOST=//p' "$render/secrets/keycloak.env"):5432/"
# The route the installer writes: the address both share (scripts/fixed-addresses.sh).
# shellcheck source=scripts/fixed-addresses.sh
. scripts/fixed-addresses.sh
export PROXY_IDP_UPSTREAM="$BUNDLED_IDP_ADDRESS:$BUNDLED_IDP_PORT"
[ -n "$login" ] && [ -n "$directory" ] || fail "the render script named no address"
# The worker too: no other pass starts it, and it reads the staff directory.
compose up -d --wait idp-db idp worker
compose up -d --force-recreate --wait proxy
wait_for_proxy
image="$(docker inspect -f '{{.Config.Image}}' "$(compose ps -q web)")"
idp_path=/idp/realms/openplanner/.well-known/openid-configuration

[ "$(fetch web "$login")" = "stand-in idp" ] || fail "web does not reach the login at its address ($login)"
[ "$(fetch worker "$directory")" = "stand-in idp" ] \
  || fail "the worker does not reach the staff directory at its address ($directory)"
[ "$(fetch idp "$database")" = "stand-in idp-db" ] \
  || fail "the identity provider does not reach its database at its address ($database)"
[ "$(proxied "$idp_path")" = "stand-in idp 200" ] || fail "/idp does not reach $PROXY_IDP_UPSTREAM"
[ "$(status_of https://booking.test:18443/healthz)" = "200" ] || fail "the proxy does not reach web"

impostor idp-db idp-store 5432 idp
[ "$(fetch idp "$database")" = "refused" ] || fail "Keycloak's database hop reached something while idp-db was down"
untouched idp-db

impostor idp edge 8080 web proxy worker
[ "$(fetch web "$login")" = "refused" ] || fail "web's login reached something while idp was down"
[ "$(fetch web "$directory")" = "refused" ] || fail "web's staff directory reached something while idp was down"
[ "$(fetch worker "$directory")" = "refused" ] || fail "the worker's staff directory reached something while idp was down"
[ "$(proxied "$idp_path")" = " 502" ] || fail "/idp answered while idp was down"
untouched idp

impostor web edge 8000 proxy
[ "$(proxied /healthz)" = " 502" ] || fail "the proxy reached something while web was down"
untouched web

for name in idp idp-db web; do docker rm -f "$project-impostor-$name" >/dev/null; done
compose up -d --wait web idp-db idp
echo "pass 4 ok: login, staff directory, database, /idp and web at fixed addresses; each fails closed while its container is down, and an impostor answering its name gets nothing"
unset PROXY_INTERNAL PROXY_IDP_UPSTREAM

# A half-configured own certificate must stop the container, never serve plain HTTP.
export PROXY_CERT_FILE=/pebble/own.crt
unset PROXY_KEY_FILE
compose up -d --force-recreate proxy >/dev/null 2>&1 || true
sleep 3
compose logs proxy 2>&1 | grep -q 'TLS_CERT_FILE and TLS_KEY_FILE go together' \
  || fail "a certificate without its key did not stop the proxy"
echo "proxy check passed"
