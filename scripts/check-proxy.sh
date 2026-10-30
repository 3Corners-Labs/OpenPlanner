#!/usr/bin/env sh
# Start the compose stack with --profile tls against Pebble, Let's Encrypt's test ACME CA, and
# check what ADR 0030, ADR 0064 and ADR 0066 promise: a certificate is obtained without manual
# steps, port 80 only redirects, public form bodies are capped, the proxy runs as non-root; a
# pair handed over through the portal-certs volume is served within the poll interval and ACME
# applies again when it is withdrawn; then the same proxy serves an own certificate from files,
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

edge_checks() { # redirect, body limit, non-root: the same in every pass
  redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H 'Host: booking.test' 'http://127.0.0.1:18080/g/x?a=1')"
  [ "$redirect" = "308 https://booking.test/g/x?a=1" ] || fail "port 80 does not redirect ($redirect)"
  # shellcheck disable=SC2086
  status="$(head -c 100000 /dev/zero | curl -sk $resolve -o /dev/null -w '%{http_code}' -X POST --data-binary @- https://booking.test:18443/g/x)"
  [ "$status" = "413" ] || fail "oversized form body not refused ($status)"
  [ "$(compose exec -T proxy id -u)" != "0" ] || fail "proxy runs as root"
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
tls_check untrusted Pebble
echo "pass 1 ok: certificate obtained over ACME, redirect, body limit, non-root, tls-check"

self_signed() { # self_signed NAME ORGANISATION: a 30-day EC pair for booking.test in $certs
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 30 \
    -subj "/O=$2/CN=booking.test" -addext "subjectAltName=DNS:booking.test" \
    -keyout "$certs/$1.key" -out "$certs/$1.crt" >/dev/null 2>&1
}

wait_for_issuer() { # wait_for_issuer FRAGMENT: the watcher polls every 10 s (ADR 0066)
  for _ in $(seq 1 20); do
    if issuer | grep -q "$1"; then return 0; fi
    sleep 2
  done
  fail "the proxy did not switch to '$1' ($(issuer))"
}

# Pass 1b: the pair the superuser uploads in the portal reaches the proxy through the
# portal-certs volume (ADR 0066): written by web as fullchain.pem, privkey.pem and serial.
self_signed portal "OpenPlanner portal check"
compose cp "$certs/portal.crt" web:/srv/portal-certs/fullchain.pem
compose cp "$certs/portal.key" web:/srv/portal-certs/privkey.pem
compose exec -T web sh -c 'chmod 600 /srv/portal-certs/privkey.pem && echo portal-1 > /srv/portal-certs/serial'
wait_for_issuer 'OpenPlanner portal check'
edge_checks
tls_check untrusted "OpenPlanner portal check"
compose exec -T web sh -c 'rm /srv/portal-certs/serial /srv/portal-certs/privkey.pem /srv/portal-certs/fullchain.pem'
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
tls_check untrusted "OpenPlanner own CA check"
echo "pass 2 ok: own certificate from files, redirect, body limit, non-root, tls-check"

# A half-configured own certificate must stop the container, never serve plain HTTP.
unset PROXY_KEY_FILE
compose up -d --force-recreate proxy >/dev/null 2>&1 || true
sleep 3
compose logs proxy 2>&1 | grep -q 'TLS_CERT_FILE and TLS_KEY_FILE go together' \
  || fail "a certificate without its key did not stop the proxy"
echo "proxy check passed"
