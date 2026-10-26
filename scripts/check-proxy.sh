#!/usr/bin/env sh
# Start the compose stack with --profile tls against Pebble, Let's Encrypt's test ACME CA, and
# check what ADR 0029 promises: a certificate is obtained without manual steps, port 80 only
# redirects, public form bodies are capped, the proxy runs as non-root. Used by CI and by
# `make proxy-check`. Needs docker with compose v2 and curl.
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

pebble_container="$(docker create ghcr.io/letsencrypt/pebble:2.8.0)"
docker cp "$pebble_container:/test/certs/pebble.minica.pem" "$certs/"
docker rm "$pebble_container" >/dev/null
chmod a+r "$certs/pebble.minica.pem"

compose up -d --build --wait proxy

resolve="--resolve booking.test:18443:127.0.0.1"
for _ in $(seq 1 30); do
  # shellcheck disable=SC2086
  if curl -sk $resolve -o /dev/null https://booking.test:18443/healthz; then break; fi
  sleep 2
done

fail() { echo "proxy check failed: $1" >&2; compose logs proxy >&2; exit 1; }

# shellcheck disable=SC2086
issuer="$(curl -skv $resolve -o /dev/null https://booking.test:18443/healthz 2>&1 | grep 'issuer:' || true)"
echo "$issuer" | grep -q 'Pebble' || fail "no certificate from the ACME CA ($issuer)"

redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H 'Host: booking.test' 'http://127.0.0.1:18080/g/x?a=1')"
[ "$redirect" = "308 https://booking.test/g/x?a=1" ] || fail "port 80 does not redirect ($redirect)"

# shellcheck disable=SC2086
status="$(head -c 100000 /dev/zero | curl -sk $resolve -o /dev/null -w '%{http_code}' -X POST --data-binary @- https://booking.test:18443/g/x)"
[ "$status" = "413" ] || fail "oversized form body not refused ($status)"

[ "$(compose exec -T proxy id -u)" != "0" ] || fail "proxy runs as root"

echo "proxy check passed: certificate obtained over ACME, redirect, body limit, non-root"
