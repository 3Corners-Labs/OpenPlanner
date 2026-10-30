#!/bin/sh
# Start Caddy with one of three certificate sources (ADR 0030, ADR 0064, ADR 0066), in this
# order of precedence:
#   operator files    TLS_CERT_FILE and TLS_KEY_FILE under /certs (proxy.env): Caddy serves the
#                     files and manages nothing; renewal is the operator's, and the portal's
#                     choice is ignored.
#   portal pair       the pair the superuser uploaded in the portal, handed over by web in
#                     /portal-certs (fullchain.pem, privkey.pem and serial, written last). A
#                     watcher reloads Caddy whenever serial changes: with the pair, or without
#                     it so ACME applies again.
#   ACME (default)    TLS_DOMAIN and ACME_EMAIL; Caddy obtains and renews the certificate.
# The Caddyfile cannot branch, so the `tls` line of the https site comes from TLS_DIRECTIVE,
# which this script sets for `caddy run` and again for every `caddy reload`. Without a domain
# Caddy would serve plain HTTP on 8443, so a missing or incomplete setting stops the container.
set -eu

PORTAL_DIR=${TLS_PORTAL_DIR:-/portal-certs} # the volume's mount point (tests point it elsewhere)
POLL=${TLS_PORTAL_POLL_SECONDS:-10}
CADDYFILE=/etc/caddy/Caddyfile

fail() { echo "$1" >&2; exit 1; }

[ -n "${TLS_DOMAIN:-}" ] || fail "TLS_DOMAIN is required"

# portal_serial: the fingerprint web wrote last, or nothing while the portal pair is absent.
portal_serial() {
  if [ -s "$PORTAL_DIR/serial" ] && [ -r "$PORTAL_DIR/fullchain.pem" ] \
    && [ -r "$PORTAL_DIR/privkey.pem" ]; then
    head -n 1 "$PORTAL_DIR/serial"
  fi
}

# directive_for SERIAL: the `tls` line for the portal pair, or none so ACME applies.
directive_for() {
  if [ -n "$1" ]; then
    echo "tls $PORTAL_DIR/fullchain.pem $PORTAL_DIR/privkey.pem"
  else
    echo ""
  fi
}

# watch APPLIED: every POLL seconds, reload Caddy when the portal's serial changed; ends with
# Caddy (the process this shell becomes with `exec` below keeps its PID).
watch() {
  applied=$1
  caddy_pid=$$
  while sleep "$POLL"; do
    kill -0 "$caddy_pid" 2>/dev/null || exit 0
    current=$(portal_serial)
    [ "$current" != "$applied" ] || continue
    TLS_DIRECTIVE=$(directive_for "$current")
    export TLS_DIRECTIVE
    if caddy reload --config "$CADDYFILE" --adapter caddyfile; then
      applied=$current
      if [ -n "$current" ]; then
        echo "portal certificate $current applied; renewal is the portal's"
      else
        echo "portal certificate withdrawn; back to ACME"
      fi
    else
      echo "reload for the portal certificate failed; the previous one stays" >&2
    fi
  done
}

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
  serial=$(portal_serial)
  TLS_DIRECTIVE=$(directive_for "$serial")
  export TLS_DIRECTIVE
  [ -z "$serial" ] || echo "portal certificate $serial from $PORTAL_DIR; renewal is the portal's"
  watch "$serial" &
fi
exec caddy run --config "$CADDYFILE" --adapter caddyfile
