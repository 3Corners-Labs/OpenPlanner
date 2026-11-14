#!/bin/sh
# Start Caddy with one of four certificate sources (ADR 0030, ADR 0064, ADR 0070, ADR 0112), in
# this order of precedence:
#   operator files    TLS_CERT_FILE and TLS_KEY_FILE under /certs (proxy.env): Caddy serves the
#                     files and manages nothing; renewal is the operator's, and the portal's
#                     choice is ignored.
#   internal          TLS_INTERNAL=true (proxy.env): Caddy's own certificate authority issues and
#                     renews; the root certificate is copied to /internal-ca for the checks of
#                     the host and for download. The CA's key stays in /data. The portal's
#                     choice is ignored.
#   portal pair       the own certificate managed in the portal: its key made or imported on
#                     this host by web in /portal-certs (privkey.pem), the chain web places next
#                     to it (fullchain.pem) and serial, written last. A watcher reloads Caddy
#                     whenever serial changes: with the pair, or without it so ACME applies.
#   ACME (default)    TLS_DOMAIN and ACME_EMAIL; Caddy obtains and renews the certificate.
# The Caddyfile cannot branch, so the `tls` line of the https site comes from TLS_DIRECTIVE,
# which this script sets for `caddy run` and again for every `caddy reload`. Without a domain
# Caddy would serve plain HTTP on 8443, so a missing or incomplete setting stops the container.
# WEB_UPSTREAM (address:port) is where everything else goes: web's fixed address on edge, set in
# docker-compose.yml. IDP_UPSTREAM (address:port) adds the route /idp/* to the bundled identity
# provider (ADR 0112) at its fixed address, written by the installer. A name works but is
# named in the log: this host's DNS may answer it while its container is down (ADR 0125).
set -eu

PORTAL_DIR=${TLS_PORTAL_DIR:-/portal-certs} # the volume's mount point (tests point it elsewhere)
INTERNAL_CA_DIR=${TLS_INTERNAL_CA_DIR:-/internal-ca}
CA_ROOT=${TLS_CA_ROOT:-/data/caddy/pki/authorities/local/root.crt}
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

# publish_root: copy Caddy's root certificate (public) to the volume the checks read, once Caddy
# has made it. The authority's key is never copied.
publish_root() {
  caddy_pid=$$
  while kill -0 "$caddy_pid" 2>/dev/null; do
    if [ -s "$CA_ROOT" ]; then
      # Caddy keeps its root at mode 600; the copy is a public certificate.
      cp "$CA_ROOT" "$INTERNAL_CA_DIR/root.crt.tmp" \
        && chmod 644 "$INTERNAL_CA_DIR/root.crt.tmp" \
        && mv "$INTERNAL_CA_DIR/root.crt.tmp" "$INTERNAL_CA_DIR/root.crt"
      echo "internal CA root published to $INTERNAL_CA_DIR/root.crt"
      return 0
    fi
    sleep 2
  done
}

# upstream NAME VALUE: refuse anything but host:port, since the value goes into the Caddyfile,
# and say so when it is a name rather than an address (ADR 0125 step 2).
upstream() {
  case $2 in
    '' | *[!A-Za-z0-9.:_-]*) fail "$1 must be host:port" ;;
    *[!0-9.:]*) echo "warning: $1=$2 is a name, which this host's DNS may answer while its container is down; docker-compose.yml and scripts/install.sh give its fixed address (ADR 0125)" >&2 ;;
  esac
}

# A docker-compose.yml from before ADR 0125 step 2 sets no WEB_UPSTREAM: this image then finds
# web by its name as the earlier one did, until the checkout of this release is in place (a
# portal update replaces the image before the operator updates the checkout; docs/install.md,
# Upgrading). The fallback goes a minor later (ADR 0116: expand, then contract).
WEB_UPSTREAM=${WEB_UPSTREAM:-web:8000}
upstream WEB_UPSTREAM "$WEB_UPSTREAM"
export WEB_UPSTREAM

if [ -n "${IDP_UPSTREAM:-}" ]; then
  upstream IDP_UPSTREAM "$IDP_UPSTREAM"
  IDP_DIRECTIVE="handle /idp/* {
		reverse_proxy $IDP_UPSTREAM
	}"
else
  IDP_DIRECTIVE=""
fi
export IDP_DIRECTIVE

if [ -n "${TLS_CERT_FILE:-}" ] || [ -n "${TLS_KEY_FILE:-}" ]; then
  [ -n "${TLS_CERT_FILE:-}" ] && [ -n "${TLS_KEY_FILE:-}" ] \
    || fail "TLS_CERT_FILE and TLS_KEY_FILE go together"
  for file in "$TLS_CERT_FILE" "$TLS_KEY_FILE"; do
    [ -r "$file" ] || fail "$file is missing or not readable by uid 10001"
  done
  export TLS_DIRECTIVE="tls $TLS_CERT_FILE $TLS_KEY_FILE"
  echo "own certificate from $TLS_CERT_FILE: renewal is yours; restart the proxy after replacing it"
elif [ "${TLS_INTERNAL:-}" = true ]; then
  [ -w "$INTERNAL_CA_DIR" ] || fail "$INTERNAL_CA_DIR is not writable by uid 10001 (volume internal-ca)"
  export TLS_DIRECTIVE="tls internal"
  publish_root &
  echo "internal certificate authority: browsers trust it once its root is installed (ADR 0112)"
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
