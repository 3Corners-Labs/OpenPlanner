#!/bin/sh
# Start Caddy with one of two certificate sources (ADR 0030, ADR 0064):
#   ACME (default)    TLS_DOMAIN and ACME_EMAIL; Caddy obtains and renews the certificate.
#   own certificate   TLS_DOMAIN, TLS_CERT_FILE and TLS_KEY_FILE under /certs; Caddy serves
#                     the files and manages nothing, so renewal is the operator's.
# The Caddyfile cannot branch, so the `tls` line of the https site comes from TLS_DIRECTIVE,
# which this script sets. Without a domain Caddy would serve plain HTTP on 8443, so a missing
# or incomplete setting stops the container instead.
set -eu

fail() { echo "$1" >&2; exit 1; }

[ -n "${TLS_DOMAIN:-}" ] || fail "TLS_DOMAIN is required"
if [ -n "${TLS_CERT_FILE:-}" ] || [ -n "${TLS_KEY_FILE:-}" ]; then
  [ -n "${TLS_CERT_FILE:-}" ] && [ -n "${TLS_KEY_FILE:-}" ] \
    || fail "TLS_CERT_FILE and TLS_KEY_FILE go together"
  for file in "$TLS_CERT_FILE" "$TLS_KEY_FILE"; do
    [ -r "$file" ] || fail "$file is missing or not readable by uid 10001"
  done
  export TLS_DIRECTIVE="tls $TLS_CERT_FILE $TLS_KEY_FILE"
  echo "own certificate from $TLS_CERT_FILE: renewal is yours; restart the proxy after replacing it"
else
  [ -n "${ACME_EMAIL:-}" ] \
    || fail "ACME_EMAIL is required (or TLS_CERT_FILE and TLS_KEY_FILE for an own certificate)"
  export TLS_DIRECTIVE=""
fi
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
