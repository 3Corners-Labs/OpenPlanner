#!/usr/bin/env bash
# Render the bundled identity provider's realm (ADR 0090 step 1).
#
# Reads secrets/keycloak.env (created if missing), generates every value that is not there yet,
# and renders deploy/keycloak/realm.template.json into secrets/keycloak-realm.json, which the
# compose profile `idp` mounts. Both files get mode 600. Nothing a value holds is ever printed:
# an operator reads secrets/keycloak.env on the host, with `cat`, when they need one.
#
# Values (secrets/keycloak.env):
#   KC_BOOTSTRAP_ADMIN_PASSWORD   Keycloak admin console, user `admin` (generated)
#   KC_OPENPLANNER_CLIENT_SECRET  client `openplanner-admin`; must equal OIDC_CLIENT_SECRET in
#                                 .env (generated; the installer copies it, ADR 0090 step 2)
#   KC_DIRECTORY_CLIENT_SECRET    service account `openplanner-directory` that manages staff
#                                 (roles manage-users and view-realm only, ADR 0095); must equal
#                                 KEYCLOAK_DIRECTORY_CLIENT_SECRET in .env (generated)
#   KC_SUPER_PASSWORD             first password of the portal user `super`, changed at the
#                                 first login (generated)
#   KC_OPENPLANNER_BASE_URL       the portal's address for the redirect URIs: PUBLIC_BASE_URL
#                                 from .env, else http://127.0.0.1:8000
#   KC_HOSTNAME                   the address browsers reach Keycloak on (default loopback)
#
# A shell script, not a `python -m app.cli` command: it runs on the host before any container
# exists, like scripts/install.sh, and the host has no Python of its own.
#
# Usage: scripts/render-keycloak-realm.sh [--dir CHECKOUT]
set -euo pipefail

DIR=$(cd "$(dirname "$0")/.." && pwd)
while (($#)); do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

TEMPLATE=$DIR/deploy/keycloak/realm.template.json
SECRETS_DIR=$DIR/secrets
ENV_FILE=$SECRETS_DIR/keycloak.env
REALM_FILE=$SECRETS_DIR/keycloak-realm.json
GENERATED=(KC_BOOTSTRAP_ADMIN_PASSWORD KC_OPENPLANNER_CLIENT_SECRET KC_DIRECTORY_CLIENT_SECRET KC_SUPER_PASSWORD)
KEYS=("${GENERATED[@]}" KC_OPENPLANNER_BASE_URL KC_HOSTNAME)

log() { echo "render-keycloak-realm: $*" >&2; }
secret() { head -c 48 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

# The value of KEY in an env file (KEY=value lines; a surrounding pair of quotes is stripped).
read_var() {
  local file=$1 key=$2 line
  [[ -f $file ]] || return 0
  line=$(grep -m1 "^${key}=" "$file" || true)
  line=${line#*=}
  line=${line#\"}; line=${line%\"}
  printf '%s' "$line"
}

[[ -f $TEMPLATE ]] || { log "template not found: $TEMPLATE"; exit 1; }
umask 077
mkdir -p "$SECRETS_DIR"

# Existing values win; missing ones are generated or defaulted.
for key in "${KEYS[@]}"; do
  declare "$key=$(read_var "$ENV_FILE" "$key")"
done
generated=()
for key in "${GENERATED[@]}"; do
  [[ -n ${!key} ]] || { declare "$key=$(secret)"; generated+=("$key"); }
done
[[ -n $KC_OPENPLANNER_BASE_URL ]] || KC_OPENPLANNER_BASE_URL=$(read_var "$DIR/.env" PUBLIC_BASE_URL)
[[ -n $KC_OPENPLANNER_BASE_URL ]] || KC_OPENPLANNER_BASE_URL=http://127.0.0.1:8000
KC_OPENPLANNER_BASE_URL=${KC_OPENPLANNER_BASE_URL%/}
[[ -n $KC_HOSTNAME ]] || KC_HOSTNAME=http://127.0.0.1:8080

# A value goes into a JSON string through sed: only characters that need no escaping in either.
for key in "${KEYS[@]}"; do
  if [[ ! ${!key} =~ ^[A-Za-z0-9_.:/-]+$ ]]; then
    log "$key holds a character the realm template cannot carry (allowed: A-Z a-z 0-9 _ . : / -)"
    exit 1
  fi
done

tmp=$(mktemp "$SECRETS_DIR/.keycloak.env.XXXXXX")
{
  printf '# Written by scripts/render-keycloak-realm.sh (ADR 0090). Holds secrets: mode 600,\n'
  printf '# never copied off this host. Re-run the script after a change to render the realm.\n'
  for key in "${KEYS[@]}"; do printf '%s=%s\n' "$key" "${!key}"; done
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"

tmp=$(mktemp "$SECRETS_DIR/.keycloak-realm.json.XXXXXX")
sed -e "s|\${KC_OPENPLANNER_CLIENT_SECRET}|$KC_OPENPLANNER_CLIENT_SECRET|g" \
  -e "s|\${KC_DIRECTORY_CLIENT_SECRET}|$KC_DIRECTORY_CLIENT_SECRET|g" \
  -e "s|\${KC_SUPER_PASSWORD}|$KC_SUPER_PASSWORD|g" \
  -e "s|\${KC_OPENPLANNER_BASE_URL}|$KC_OPENPLANNER_BASE_URL|g" \
  "$TEMPLATE" > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$REALM_FILE"

((${#generated[@]} == 0)) || log "generated ${generated[*]} in $ENV_FILE"
log "rendered $REALM_FILE (portal at $KC_OPENPLANNER_BASE_URL, Keycloak at $KC_HOSTNAME)"
