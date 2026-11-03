#!/usr/bin/env sh
# Start the compose stack with --profile tls against Pebble, Let's Encrypt's test ACME CA, and
# check what ADR 0030, ADR 0064 and ADR 0070 promise: a certificate is obtained without manual
# steps, port 80 redirects browser paths and refuses machine paths (/api, /webhooks) with 403,
# the proxy passes only what the app should see (TRACE, /static/, no-store on HTML), public form bodies are capped, the proxy runs as non-root; the
# standards of ADR 0086 hold (TLS 1.2 and 1.3 only with AEAD suites, HSTS of at least a year and
# none on port 80, and the app's round trip through the proxy is `ok`, or `proxy_not_trusted` with
# a wrong FORWARDED_ALLOW_IPS); a forged X-Forwarded-For/-Proto/-Host from the visitor is replaced
# by the proxy before it reaches web (ASVS V4.1.3, V15.3.4; read from the wire); a
# pair handed over through the portal-certs volume is served within the poll interval and ACME
# applies again when it is withdrawn; then the same proxy serves an own certificate from files and
# one issued by its internal authority (ADR 0111; the root is published, trusted by `tls-check`,
# and /idp/ is routed),
# and `tls-check` from the web container reads either certificate and judges it. Used by CI
# and by `make proxy-check`. Needs docker with compose v2, curl and openssl.
set -eu

project=openplanner-proxy-check
certs="$(mktemp -d)"
compose() {
  PEBBLE_CERTS="$certs" docker compose -p "$project" \
    -f docker-compose.yml -f deploy/proxy/compose.pebble.yml --profile tls "$@"
}
cleanup() {
  compose down -v >/dev/null 2>&1 || true
  rm -rf "$certs"
}
trap cleanup EXIT

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

forged_header_check() { # a forged X-Forwarded-* is replaced at the edge (ASVS V4.1.3, V15.3.4)
  # The visitor claims to be 203.0.113.7 (a documentation address, RFC 5737), to have used http
  # and to be asking for another host. Caddy has no trusted_proxies, so it must replace all
  # three. The only place that shows what web is given is the wire between proxy and web: a
  # sniffer in web's network namespace prints the X-Forwarded-* lines of that request, so the
  # app needs no hook and no address is ever stored or logged.
  web="$(compose ps -q web)"
  image="$(docker inspect -f '{{.Config.Image}}' "$web")"
  sniffer="$(docker run -d --rm --network "container:$web" --cap-add NET_RAW --user 0 \
    --entrypoint python "$image" -I -c "$sniffer_code" 30)"
  for _ in $(seq 1 30); do
    docker logs "$sniffer" 2>&1 | grep -q '^ready$' && break
    sleep 1
  done
  # shellcheck disable=SC2086
  curl -sk $resolve -o /dev/null -H 'X-Forwarded-For: 203.0.113.7' -H 'X-Forwarded-Proto: http' \
    -H 'X-Forwarded-Host: forged.example' https://booking.test:18443/healthz
  sleep 2
  seen="$(docker logs "$sniffer" 2>&1 | tr -d '\r')"
  docker stop "$sniffer" >/dev/null 2>&1 || true
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

# Pass 1: ACME against Pebble.
compose up -d --build --wait proxy
wait_for_proxy
issuer | grep -q 'Pebble' || fail "no certificate from the ACME CA ($(issuer))"
edge_checks
round_trip ok pebble
tls_check untrusted Pebble
echo "pass 1 ok: certificate obtained over ACME, redirect, body limit, non-root, TLS versions, HSTS, round trip, tls-check"

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

# Pass 3: Caddy's internal authority (ADR 0111). No ACME, no mailbox; the public root is
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
unset PROXY_INTERNAL PROXY_IDP_UPSTREAM

# A half-configured own certificate must stop the container, never serve plain HTTP.
export PROXY_CERT_FILE=/pebble/own.crt
unset PROXY_KEY_FILE
compose up -d --force-recreate proxy >/dev/null 2>&1 || true
sleep 3
compose logs proxy 2>&1 | grep -q 'TLS_CERT_FILE and TLS_KEY_FILE go together' \
  || fail "a certificate without its key did not stop the proxy"
echo "proxy check passed"
