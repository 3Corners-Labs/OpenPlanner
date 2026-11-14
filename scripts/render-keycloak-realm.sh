#!/usr/bin/env bash
# Render the bundled identity provider's realm (ADR 0090 step 1).
#
# Reads secrets/keycloak.env (created if missing), generates every value that is not there yet,
# and renders deploy/keycloak/realm.template.json into secrets/keycloak-realm.json, which the
# compose profile `idp` mounts. It also writes the application's half, secrets/keycloak-app.env
# (KEYCLOAK_ADMIN_URL, KEYCLOAK_DIRECTORY_CLIENT_ID and _SECRET), which web and worker read, so
# staff management works without copying a value by hand (ADR 0106). The same
# file carries the login (OIDC_*, ADR 0106 step 5). With an https address (ADR 0112) Keycloak is
# served by the proxy under /idp. The env files get mode
# 600; the realm gets 644, because Keycloak reads it as its own user (uid 1000), which is
# rarely the host user. The secrets directory is 700, so nobody else on the host gets past
# it; the container reads the file through its bind mount. Nothing a value holds is ever printed:
# an operator reads secrets/keycloak.env on the host, with `cat`, when they need one.
#
# Values (secrets/keycloak.env):
#   KC_BOOTSTRAP_ADMIN_PASSWORD   Keycloak admin console, user `admin` (generated)
#   KC_OPENPLANNER_CLIENT_SECRET  client `openplanner-admin`; must equal OIDC_CLIENT_SECRET in
#                                 .env (generated; the installer copies it, ADR 0090 step 2)
#   KC_DIRECTORY_CLIENT_SECRET    service account `openplanner-directory` that manages staff
#                                 (roles manage-users and view-realm only, ADR 0095); written
#                                 to secrets/keycloak-app.env as KEYCLOAK_DIRECTORY_CLIENT_SECRET
#                                 (generated)
#   KC_SUPER_PASSWORD             first password of the portal user `super`, changed at the
#                                 first login (generated)
#   KC_OPENPLANNER_BASE_URL       the portal's address for the redirect URIs: PUBLIC_BASE_URL
#                                 from .env, else http://127.0.0.1:8000
#   KC_HOSTNAME                   the address browsers reach Keycloak on (default loopback)
#   KC_CLIENT_SECRETS_PINNED      `true` once this script made both client secrets itself, since
#                                 every hop has a fixed address (ADR 0125 step 2); without it the
#                                 installer replaces them, as they may have reached the name idp
#
# With --database postgres (production, ADR 0106 point 4) Keycloak runs on its own PostgreSQL
# container `idp-db`: the script also generates KC_DB_PASSWORD and writes the connection to
# secrets/keycloak.env (KC_DB*, the database at its fixed address, ADR 0125 step 2;
# KC_HTTP_ENABLED: TLS ends at the proxy) and the container's own secrets/idp-db.env. The
# choice is remembered in keycloak.env (KC_DB).
#
# A shell script, not a `python -m app.cli` command: it runs on the host before any container
# exists, like scripts/install.sh, and the host has no Python of its own.
#
# Usage: scripts/render-keycloak-realm.sh [--dir CHECKOUT] [--database h2|postgres]
set -euo pipefail

DIR=$(cd "$(dirname "$0")/.." && pwd)
DATABASE=''
while (($#)); do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --database) DATABASE=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case $DATABASE in '' | h2 | postgres) ;; *) echo "--database must be h2 or postgres" >&2; exit 2 ;; esac

TEMPLATE=$DIR/deploy/keycloak/realm.template.json
SECRETS_DIR=$DIR/secrets
ENV_FILE=$SECRETS_DIR/keycloak.env
REALM_FILE=$SECRETS_DIR/keycloak-realm.json
APP_ENV_FILE=$SECRETS_DIR/keycloak-app.env
# ADR 0125 step 2: web and worker reach Keycloak, and Keycloak its database, at the fixed
# addresses docker-compose.yml gives them on its internal networks, never by a name this host's
# DNS could answer while the container is down. scripts/install.sh (IDP_UPSTREAM) and
# app/domain/web_transport.py hold the same address; a test keeps them equal.
IDP_ADDRESS=172.31.240.4
IDP_DB_ADDRESS=172.31.240.18
# How web and worker reach the realm's admin API.
ADMIN_URL=http://$IDP_ADDRESS:8080/admin/realms/openplanner
GENERATED=(KC_BOOTSTRAP_ADMIN_PASSWORD KC_OPENPLANNER_CLIENT_SECRET KC_DIRECTORY_CLIENT_SECRET KC_SUPER_PASSWORD)
KEYS=("${GENERATED[@]}" KC_OPENPLANNER_BASE_URL KC_HOSTNAME)
DB_FILE=$SECRETS_DIR/idp-db.env

log() { echo "render-keycloak-realm: $*" >&2; }
secret() { head -c 48 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

# The value of KEY in an env file (KEY=value lines; a surrounding pair of quotes is stripped).
read_var() {
  local file=$1 key=$2 line
  [[ -f $file ]] || return 0
  line=$(grep -m1 "^${key}=" "$file" || true)
  line=${line#*=}
  line=${line#\"}; line=${line%\"}
  line=${line#\'}; line=${line%\'} # the installer writes KEY='value'
  printf '%s' "$line"
}

[[ -f $TEMPLATE ]] || { log "template not found: $TEMPLATE"; exit 1; }
umask 077
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

# Existing values win; missing ones are generated or defaulted.
for key in "${KEYS[@]}"; do
  declare "$key=$(read_var "$ENV_FILE" "$key")"
done
# ADR 0125 step 2: a pair made here never travelled to the name idp, because every hop now has a
# fixed address; the mark stays with the pair. A kept pair without it may have, and the installer
# replaces it (new_client_secrets).
KC_CLIENT_SECRETS_PINNED=$(read_var "$ENV_FILE" KC_CLIENT_SECRETS_PINNED)
[[ -n $KC_OPENPLANNER_CLIENT_SECRET || -n $KC_DIRECTORY_CLIENT_SECRET ]] || KC_CLIENT_SECRETS_PINNED=true
generated=()
for key in "${GENERATED[@]}"; do
  [[ -n ${!key} ]] || { declare "$key=$(secret)"; generated+=("$key"); }
done
# The database: the flag wins, else what an earlier run chose; H2 is the development default.
[[ -n $DATABASE ]] || DATABASE=$(read_var "$ENV_FILE" KC_DB)
[[ $DATABASE == postgres ]] || DATABASE=h2
KC_DB_PASSWORD=$(read_var "$ENV_FILE" KC_DB_PASSWORD)
if [[ $DATABASE == postgres && -z $KC_DB_PASSWORD ]]; then
  KC_DB_PASSWORD=$(secret)
  generated+=(KC_DB_PASSWORD)
fi
[[ -n $KC_OPENPLANNER_BASE_URL ]] || KC_OPENPLANNER_BASE_URL=$(read_var "$DIR/.env" PUBLIC_BASE_URL)
[[ -n $KC_OPENPLANNER_BASE_URL ]] || KC_OPENPLANNER_BASE_URL=http://127.0.0.1:8000
KC_OPENPLANNER_BASE_URL=${KC_OPENPLANNER_BASE_URL%/}
[[ -n $KC_HOSTNAME ]] || KC_HOSTNAME=http://127.0.0.1:8080

# ADR 0112: with an https public address Keycloak is served by the proxy under /idp, on the same
# host name and certificate. The browser address, the relative path and the proxy header mode
# follow from it; web and worker talk to Keycloak at its address on the internal network, with
# the path.
if [[ $KC_OPENPLANNER_BASE_URL == https://* ]]; then
  HTTPS=1
  KC_HOSTNAME=$KC_OPENPLANNER_BASE_URL/idp
  ADMIN_URL=http://$IDP_ADDRESS:8080/idp/admin/realms/openplanner
  DISCOVERY_URL=http://$IDP_ADDRESS:8080/idp/realms/openplanner/.well-known/openid-configuration
else
  HTTPS=0
  DISCOVERY_URL=http://$IDP_ADDRESS:8080/realms/openplanner/.well-known/openid-configuration
fi

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
  [[ $KC_CLIENT_SECRETS_PINNED != true ]] || printf 'KC_CLIENT_SECRETS_PINNED=true\n'
  if [[ $DATABASE == postgres ]]; then
    printf 'KC_DB=postgres\nKC_DB_URL_HOST=%s\nKC_DB_URL_DATABASE=keycloak\n' "$IDP_DB_ADDRESS"
    printf 'KC_DB_USERNAME=keycloak\nKC_DB_PASSWORD=%s\n' "$KC_DB_PASSWORD"
    # `start` refuses plain http unless told; TLS ends at the proxy, which sets the headers.
    printf 'KC_HTTP_ENABLED=true\n'
  fi
  if ((HTTPS)); then
    printf 'KC_HTTP_RELATIVE_PATH=/idp\n'
  fi
  if ((HTTPS)) || [[ $DATABASE == postgres ]]; then printf 'KC_PROXY_HEADERS=xforwarded\n'; fi
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"

if [[ $DATABASE == postgres ]]; then
  tmp=$(mktemp "$SECRETS_DIR/.idp-db.env.XXXXXX")
  printf '# Written by scripts/render-keycloak-realm.sh (ADR 0106 point 4): the container idp-db.\nPOSTGRES_USER=keycloak\nPOSTGRES_DB=keycloak\nPOSTGRES_PASSWORD=%s\n' "$KC_DB_PASSWORD" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$DB_FILE"
fi

tmp=$(mktemp "$SECRETS_DIR/.keycloak-realm.json.XXXXXX")
sed -e "s|\${KC_OPENPLANNER_CLIENT_SECRET}|$KC_OPENPLANNER_CLIENT_SECRET|g" \
  -e "s|\${KC_DIRECTORY_CLIENT_SECRET}|$KC_DIRECTORY_CLIENT_SECRET|g" \
  -e "s|\${KC_SUPER_PASSWORD}|$KC_SUPER_PASSWORD|g" \
  -e "s|\${KC_OPENPLANNER_BASE_URL}|$KC_OPENPLANNER_BASE_URL|g" \
  "$TEMPLATE" > "$tmp"
chmod 644 "$tmp"  # read by the container's own user; the directory keeps others out
mv "$tmp" "$REALM_FILE"

tmp=$(mktemp "$SECRETS_DIR/.keycloak-app.env.XXXXXX")
{
  printf '# Written by scripts/render-keycloak-realm.sh (ADR 0106): the staff directory of the\n'
  printf '# bundled provider, read by web and worker. Holds a secret: mode 600, never copied off.\n'
  printf 'KEYCLOAK_ADMIN_URL=%s\n' "$ADMIN_URL"
  printf 'KEYCLOAK_DIRECTORY_CLIENT_ID=openplanner-directory\n'
  printf 'KEYCLOAK_DIRECTORY_CLIENT_SECRET=%s\n' "$KC_DIRECTORY_CLIENT_SECRET"
  # The login itself, so nothing has to be copied into .env by hand (ADR 0112, ADR 0106 step 5).
  # Keycloak announces KC_HOSTNAME, not the internal address it is fetched on (ADR 0097).
  printf 'OIDC_DISCOVERY_URL=%s\n' "$DISCOVERY_URL"
  printf 'OIDC_ISSUER=%s/realms/openplanner\n' "$KC_HOSTNAME"
  printf 'OIDC_CLIENT_ID=openplanner-admin\n'
  printf 'OIDC_CLIENT_SECRET=%s\n' "$KC_OPENPLANNER_CLIENT_SECRET"
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$APP_ENV_FILE"

((${#generated[@]} == 0)) || log "generated ${generated[*]} in $ENV_FILE"
log "wrote $APP_ENV_FILE (staff directory at $ADMIN_URL)"
log "rendered $REALM_FILE (portal at $KC_OPENPLANNER_BASE_URL, Keycloak at $KC_HOSTNAME)"
