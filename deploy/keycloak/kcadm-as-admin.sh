#!/bin/sh
# Run Keycloak's kcadm.sh as realm master's `admin`, inside the bundled identity provider's own
# container (ADR 0126): the one place that logs kcadm.sh in for the installer (the realm's mail
# server, realm master's loopback address), for scripts/check-proxy.sh and for the runbook.
#
# docker-compose.yml mounts this file read-only at /opt/openplanner/kcadm-as-admin.sh; run it as
#   docker compose exec -T idp sh /opt/openplanner/kcadm-as-admin.sh ARGS...
# where ARGS are kcadm.sh's own, for example `update realms/master -s attributes.frontendUrl=...`.
# It logs in on Keycloak's own address in the container (KC_HTTP_RELATIVE_PATH, /idp behind the
# proxy) with the container's KC_BOOTSTRAP_ADMIN_PASSWORD, from secrets/keycloak.env. The password
# reaches kcadm.sh through its environment (KC_CLI_PASSWORD), never as an argument, so it is not in
# any process list, on the host or in the container. A body for `-f -` comes on stdin and is passed
# through. The login is kept in a temporary file that is removed when the command ends.
set -eu

KCADM=${KCADM:-/opt/keycloak/bin/kcadm.sh} # tests point it at a stand-in
config=$(mktemp)
trap 'rm -f "$config"' EXIT
KC_CLI_PASSWORD=$KC_BOOTSTRAP_ADMIN_PASSWORD "$KCADM" config credentials --config "$config" \
  --server "http://localhost:8080${KC_HTTP_RELATIVE_PATH:-}" --realm master --user admin </dev/null >/dev/null
"$KCADM" "$@" --config "$config"
