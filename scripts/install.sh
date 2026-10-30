#!/usr/bin/env bash
# OpenPlanner host installer (docs/install.md). Run as the operator account from the checkout;
# the checkout is the installation directory (by convention /home/<user>/openplanner). It asks
# for sudo only to install the host updater (ADR 0029).
#
#   ./scripts/install.sh --mode production|test|lab [--version X.Y.Z] [--repository owner/name]
#                        [--channel release|rc] [--non-interactive] [--acme-staging]
#                        [--no-updater] [--upgrade-docker] [--no-packages]
#
#   0. host packages   Ubuntu/Debian: curl, flock, Compose plugin and cosign at their newest
#                      version on every run; Docker Engine from Docker's own repository when
#                      missing. An existing engine is upgraded only with --upgrade-docker, since
#                      that restarts every container on the host; without it, waiting updates
#                      are reported.
#   1. prerequisites   tools, Docker access, Compose version, systemd; all problems at once
#   2. .env            new: asked or from the environment, mode 600
#                      existing: repaired where no person is needed (keys set twice, mode;
#                      backup first), then checked; the rest is reported
#      secrets/        secrets.env: the bootstrap keys, apart from .env (ADR 0042 §9); moved
#                      there from .env once, generated when missing, never overwritten
#   3. proxy.env       production only: TLS_DOMAIN and ACME_EMAIL, same rules
#   4. .env.update     pull <image>:<version> -> digest -> cosign verify -> pin digest, profiles
#                      and mode; without --version an existing pin stays
#   5. start           docker compose --env-file .env.update up -d, wait for health
#   6. host updater    /usr/local/sbin, systemd units, /etc/openplanner/updater.env pointing at
#                      this directory, a drop-in that opens only this directory under /home
#
# Running it again is safe: it repairs what is missing and changes nothing that is in order.
# Non-interactive runs take every answer from environment variables of the same name as the
# setting (PUBLIC_BASE_URL, TLS_DOMAIN, OIDC_CLIENT_SECRET, ...).
set -Eeuo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
# Staging root for the files outside $DIR; only the tests set it.
ROOT=${INSTALL_ROOT:-}
ENV_FILE=$DIR/.env
PROXY_ENV=$DIR/proxy.env
PIN_FILE=$DIR/.env.update
SECRETS_FILE=$DIR/secrets/secrets.env
# The keys `python -m app.cli init` generates (app/adapters/bootstrap.py); a test keeps them equal.
BOOTSTRAP_KEYS=(SETTINGS_ENCRYPTION_KEY MANAGEMENT_TOKEN_KEY SESSION_SECRET)
UPDATER_STATE=/var/lib/openplanner-updater
UNIT_DIR=$ROOT/etc/systemd/system
OIDC_ISSUER=https://token.actions.githubusercontent.com
READY_URL=http://127.0.0.1:8000/readyz
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
# not a pin: the oldest Compose that reads env_file with path/required; packages stay newest
COMPOSE_MIN=2.24.0
VERSION_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}(-rc\.[0-9]{1,4})?$'
RELEASE_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
REPOSITORY_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'

OS_RELEASE=$ROOT/etc/os-release
# Docker's release key (https://docs.docker.com/engine/install/ubuntu/); a fixed trust anchor.
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
DOCKER_KEYRING=$ROOT/etc/apt/keyrings/docker.asc

MODE='' VERSION='' REPOSITORY='' INTERACTIVE=1 ACME_STAGING=0 UPDATER=1 PACKAGES=1 UPGRADE_DOCKER=0

log() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$1" >&2
  shift
  (($# == 0)) || printf '%s\n' "$@" >&2
  exit 1
}
usage() { sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
compose() { docker compose --project-directory "$DIR" --env-file "$PIN_FILE" "$@"; }
# A value from an env file, read as data (never sourced); the last occurrence wins, as in Compose.
value() {
  [[ -f $1 ]] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n 1 | sed "s/^'\(.*\)'\$/\1/; s/^\"\(.*\)\"\$/\1/"
}
secret() { head -c 48 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

parse_args() {
  while (($#)); do
    case $1 in
      --mode) MODE=${2:?--mode needs a value} && shift ;;
      --version) VERSION=${2:?--version needs a value} && shift ;;
      --repository) REPOSITORY=${2:?--repository needs a value} && shift ;;
      --channel) CHANNEL=${2:?--channel needs a value} && shift ;;
      --non-interactive) INTERACTIVE=0 ;;
      --acme-staging) ACME_STAGING=1 ;;
      --no-updater) UPDATER=0 ;;
      --no-packages) PACKAGES=0 ;;
      --upgrade-docker) UPGRADE_DOCKER=1 ;;
      -h | --help) usage && exit 0 ;;
      *) usage >&2 && die "unknown option: $1" ;;
    esac
    shift
  done
  [[ -t 0 ]] || INTERACTIVE=0 # no terminal to ask on
  # A rerun may leave out --mode: the pin file remembers it.
  MODE=${MODE:-$(value "$PIN_FILE" OPENPLANNER_MODE)}
  case $MODE in
    production | test | lab) ;;
    '') usage >&2 && die "--mode is required on the first run" ;;
    *) die "--mode must be production, test or lab, not '$MODE'" ;;
  esac
  # ADR 0059: the channel is remembered in .env; production follows releases only.
  CHANNEL=${CHANNEL:-$(value "$ENV_FILE" UPDATE_CHANNEL)}
  CHANNEL=${CHANNEL:-release}
  case $CHANNEL in
    release | rc) ;;
    *) die "--channel must be release or rc, not '$CHANNEL'" ;;
  esac
  [[ $MODE != production || $CHANNEL == release ]] \
    || die "--channel rc is refused in production mode: production follows releases (ADR 0059)"
  [[ -z $VERSION || $VERSION =~ $VERSION_RE ]] || die "--version must look like 1.2.3 (or 1.2.3-rc.4 on the rc channel)"
  [[ -z $VERSION || $CHANNEL == rc || $VERSION =~ $RELEASE_RE ]] \
    || die "--version $VERSION is a release candidate; it needs --channel rc"
  REPOSITORY=${REPOSITORY:-$(value "$ENV_FILE" UPDATE_REPOSITORY)}
  REPOSITORY=${REPOSITORY:-3Corners-Labs/OpenPlanner}
  [[ $REPOSITORY =~ $REPOSITORY_RE ]] || die "--repository must be owner/name"
}

# 0. Host packages ------------------------------------------------------------------------------

apt_get() { sudo env DEBIAN_FRONTEND=noninteractive apt-get -qq -o Dpkg::Options::=--force-confold "$@"; }
installed() { dpkg -s "$1" >/dev/null 2>&1; }
os_field() { sed -n "s/^$1=//p" "$OS_RELEASE" | tr -d '"'; }
github_latest() { # github_latest owner/name: the newest release tag without its v
  curl -fsS -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$1/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -n 1
}

add_docker_repository() {
  local id codename key fingerprint
  id=$(os_field ID) codename=$(os_field VERSION_CODENAME)
  log "adding Docker's apt repository for $id $codename"
  key=$(mktemp)
  curl -fsSL "https://download.docker.com/linux/$id/gpg" -o "$key" || die "cannot download Docker's release key"
  fingerprint=$(gpg --show-keys --with-colons "$key" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
  [[ $fingerprint == "$DOCKER_KEY_FINGERPRINT" ]] \
    || die "Docker's release key has fingerprint '${fingerprint:-none}', expected $DOCKER_KEY_FINGERPRINT; nothing installed"
  sudo install -d -m 755 "$(dirname "$DOCKER_KEYRING")"
  sudo install -m 644 "$key" "$DOCKER_KEYRING"
  rm -f "$key"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$id" "$codename" \
    | root_file 644 "$ROOT/etc/apt/sources.list.d/docker.list"
  apt_get update
}

# The engine restarts every container on the host when upgraded, so it waits for --upgrade-docker.
docker_engine() {
  local engine=(docker-ce docker-ce-cli containerd.io) waiting
  installed docker-ce || engine=(docker.io containerd runc)
  if ((UPGRADE_DOCKER)); then
    log "upgrading Docker Engine (${engine[*]}); containers on this host restart"
    apt_get install --only-upgrade -y "${engine[@]}"
    return
  fi
  waiting=$(apt list --upgradable 2>/dev/null | sed -n 's|^\([^/]*\)/.*|\1|p' \
    | grep -Fx -f <(printf '%s\n' "${engine[@]}") | tr '\n' ' ') || true
  [[ -z $waiting ]] \
    || warn "updates for Docker Engine are waiting (${waiting% }); run again with --upgrade-docker at a quiet moment (restarts every container on this host)"
}

ensure_cosign() {
  local latest current arch deb tmp expected actual
  latest=$(github_latest sigstore/cosign) || true
  current=$(cosign version 2>/dev/null | sed -n 's/^GitVersion: *v//p') || true
  if ! [[ $latest =~ $VERSION_RE ]]; then
    [[ -n $current ]] || die "cannot find the latest cosign release (GitHub unreachable?)"
    warn "cannot check for a newer cosign; keeping $current"
    return
  fi
  [[ $current != "$latest" ]] || return 0
  arch=$(dpkg --print-architecture)
  deb=cosign_${latest}_${arch}.deb
  log "installing cosign $latest (was: ${current:-none})"
  tmp=$(mktemp -d)
  if ! curl -fsSL -o "$tmp/$deb" "https://github.com/sigstore/cosign/releases/download/v$latest/$deb" \
    || ! curl -fsSL -o "$tmp/checksums" "https://github.com/sigstore/cosign/releases/download/v$latest/cosign_checksums.txt"; then
    die "cannot download cosign $latest"
  fi
  expected=$(awk -v name="$deb" '$2 == name {print $1}' "$tmp/checksums")
  actual=$(sha256sum "$tmp/$deb" | awk '{print $1}')
  [[ -n $expected && $actual == "$expected" ]] || die "checksum of $deb does not match its release; nothing installed"
  apt_get install -y "$tmp/$deb"
  rm -rf "$tmp"
}

ensure_packages() {
  local tools=(ca-certificates curl gnupg util-linux) compose=docker-compose-plugin
  ((PACKAGES)) || return 0
  case $(os_field ID) in
    ubuntu | debian) ;;
    *)
      warn "not Ubuntu or Debian: install the prerequisites yourself (docs/install.md)"
      return 0
      ;;
  esac
  ((EUID != 0)) || die "run as the operator account, not as root; the script uses sudo itself"
  log "bringing host packages up to date (sudo apt-get)"
  apt_get update
  # Installs what is missing and upgrades what is there to the newest the archive has.
  apt_get install -y --no-install-recommends "${tools[@]}"
  if ! command -v docker >/dev/null; then
    add_docker_repository
    apt_get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl enable --now docker
  else
    # Next to the distribution's docker.io the plugin is its own package, which not every
    # release has (Debian 12); the prerequisite check then names what is missing.
    installed docker-ce || compose=docker-compose-v2
    apt_get install -y --no-install-recommends "$compose" || warn "could not install $compose from apt"
    docker_engine
  fi
  ensure_cosign
  # The session's groups, then the account's: a new membership counts only after a new login.
  if ! id -nG | tr ' ' '\n' | grep -qx docker; then
    id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker || sudo usermod -aG docker "$(id -un)"
    die "$(id -un) is now in the docker group: log out and in again, then run this script again"
  fi
}

# 1. Prerequisites ------------------------------------------------------------------------------

version_at_least() { [[ $(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1) == "$2" ]]; }

preflight() {
  local problems=() tool compose_version
  ((EUID != 0)) || problems+=("run as the operator account, not as root; the script uses sudo itself")
  for tool in docker cosign curl flock sed sort base64 systemctl; do
    command -v "$tool" >/dev/null || problems+=("$tool is not installed (docs/install.md, Prerequisites)")
  done
  if ((UPDATER)); then
    command -v sudo >/dev/null || problems+=("sudo is not installed; needed for the host updater")
  fi
  if command -v docker >/dev/null; then
    if ! docker info >/dev/null 2>&1; then
      problems+=("cannot reach Docker as $(id -un): add the account to the docker group and log in again")
    elif ! compose_version=$(docker compose version --short 2>/dev/null); then
      problems+=("the Docker Compose plugin is missing (docker-compose-plugin / docker-compose-v2)")
    elif ! version_at_least "${compose_version#v}" "$COMPOSE_MIN"; then
      problems+=("Docker Compose $compose_version is too old; $COMPOSE_MIN or newer is needed")
    fi
  fi
  [[ -f $DIR/docker-compose.yml ]] || problems+=("$DIR has no docker-compose.yml: run the script from the checkout")
  if ((${#problems[@]})); then
    die "prerequisites not met:" "${problems[@]/#/  - }"
  fi
  log "prerequisites in order (installation directory $DIR)"
}

# 2 and 3. Settings files -----------------------------------------------------------------------

# ask VAR QUESTION DEFAULT [required|optional|secret|optional-secret]: the environment's value,
# else the answer, else the default. Secrets are read without echo. Values are written
# single-quoted, so they cannot hold ' or a line break.
ask() {
  local var=$1 question=$2 default=$3 kind=${4:-required} answer
  answer=${!var:-}
  if [[ -z $answer ]] && ((INTERACTIVE)); then
    if [[ $kind == *secret ]]; then
      read -rsp "$question: " answer && echo
    else
      read -rp "$question${default:+ [$default]}: " answer
    fi
  fi
  answer=${answer:-$default}
  if [[ -z $answer && $kind != optional* ]]; then
    die "$var is required (answer the question or set $var in the environment)"
  fi
  [[ $answer != *"'"* && $answer != *$'\n'* ]] || die "$var cannot contain a quote or a line break"
  printf -v "$var" '%s' "$answer"
}

put() { [[ -z $2 ]] || printf "%s='%s'\n" "$1" "$2"; }

# Write a file through a private temporary one, so a half-written settings file never exists.
write_private() {
  local target=$1 tmp
  tmp=$(mktemp "$target.XXXXXX")
  chmod 600 "$tmp"
  cat >"$tmp"
  mv "$tmp" "$target"
}

new_env() {
  local environment=test smtp_default='' oidc_default=''
  log "writing $ENV_FILE"
  case $MODE in
    production)
      environment=prod
      PUBLIC_BASE_URL=https://$TLS_DOMAIN
      ;;
    test) ask PUBLIC_BASE_URL "Public address of this installation" "http://$(hostname -f 2>/dev/null || hostname):8000" ;;
    lab)
      ask PUBLIC_BASE_URL "Public address of this installation" "http://127.0.0.1:8000"
      smtp_default=mailpit
      oidc_default=http://keycloak:8080/realms/openplanner/.well-known/openid-configuration
      OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET:-dev-only-openplanner-admin-secret}
      SMTP_PORT=${SMTP_PORT:-1025} SMTP_STARTTLS=${SMTP_STARTTLS:-false}
      ;;
  esac
  ask OIDC_DISCOVERY_URL "OIDC discovery URL of the identity provider (empty: no admin portal)" "$oidc_default" optional
  if [[ -n $OIDC_DISCOVERY_URL ]]; then
    ask OIDC_CLIENT_ID "OIDC client ID" openplanner-admin
    ask OIDC_CLIENT_SECRET "OIDC client secret" '' secret
  fi
  ask SMTP_HOST "SMTP server for outgoing mail (empty: organisations set their own in the portal)" "$smtp_default" optional
  if [[ -n $SMTP_HOST ]]; then
    ask SMTP_PORT "SMTP port" 587
    ask SMTP_STARTTLS "STARTTLS (true/false)" true
    ask SMTP_USERNAME "SMTP login (empty: none)" '' optional
    [[ -z $SMTP_USERNAME ]] || ask SMTP_PASSWORD "SMTP password" '' secret
    ask MAIL_FROM "Sender address" "OpenPlanner <noreply@${TLS_DOMAIN:-openplanner.invalid}>"
  fi
  ask UPDATE_CHECK_TOKEN "GitHub token with read access to $REPOSITORY (empty: public repository)" '' optional-secret
  {
    printf '# Written by scripts/install.sh (%s mode). Every setting: .env.example.\n' "$MODE"
    printf '# Holds secrets: mode 600, never copied off this host. Keys: secrets/secrets.env.\n'
    put ENVIRONMENT "$environment"
    put PUBLIC_BASE_URL "$PUBLIC_BASE_URL"
    put OIDC_DISCOVERY_URL "$OIDC_DISCOVERY_URL"
    put OIDC_CLIENT_ID "${OIDC_CLIENT_ID:-}"
    put OIDC_CLIENT_SECRET "${OIDC_CLIENT_SECRET:-}"
    put SMTP_HOST "$SMTP_HOST"
    put SMTP_PORT "${SMTP_PORT:-}"
    put SMTP_STARTTLS "${SMTP_STARTTLS:-}"
    put SMTP_USERNAME "${SMTP_USERNAME:-}"
    put SMTP_PASSWORD "${SMTP_PASSWORD:-}"
    put MAIL_FROM "${MAIL_FROM:-}"
    put UPDATE_REPOSITORY "$REPOSITORY"
    put UPDATE_CHECK_TOKEN "$UPDATE_CHECK_TOKEN"
    put UPDATE_CHANNEL "$CHANNEL"
    printf '# Webex and WxCC: docs/runbook.md, "Webex meetings and Webex Contact Center".\n'
  } | write_private "$ENV_FILE"
}

env_keys() { sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$1"; }

# Rewrite .env through awk, after a private backup; prints the backup's name.
rewrite_env() {
  local backup
  backup=$ENV_FILE.bak.$(date -u +%Y%m%dT%H%M%SZ)
  [[ -f $backup ]] || { cp "$ENV_FILE" "$backup" && chmod 600 "$backup"; }
  awk "$@" "$ENV_FILE" | write_private "$ENV_FILE"
  printf '%s' "$backup"
}

# What can be repaired without a person: keys set twice (the last one is the one Compose uses, so
# the earlier ones go, after a backup) and the file mode.
repair_env() {
  local duplicates backup
  duplicates=$(env_keys "$ENV_FILE" | sort | uniq -d | tr '\n' ' ')
  if [[ -n $duplicates ]]; then
    # shellcheck disable=SC2016 # an awk program
    backup=$(rewrite_env '{ line[NR] = $0; key[NR] = "" }
      /^[A-Za-z_][A-Za-z0-9_]*=/ { key[NR] = substr($0, 1, index($0, "=") - 1); last[key[NR]] = NR }
      END { for (i = 1; i <= NR; i++) if (key[i] == "" || last[key[i]] == i) print line[i] }')
    warn "$ENV_FILE set ${duplicates% } more than once; kept the last value of each (the one in use), backup: $backup"
  fi
  chmod 600 "$ENV_FILE"
  set_channel
}

# UPDATE_CHANNEL in .env follows --channel (ADR 0059): replaced when it differs, added when
# missing and not the default. The application reads it from .env, the updater's identity below.
set_channel() {
  local current backup
  current=$(value "$ENV_FILE" UPDATE_CHANNEL)
  [[ ${current:-release} != "$CHANNEL" ]] || return 0
  # shellcheck disable=SC2016 # an awk program
  backup=$(rewrite_env -v channel="$CHANNEL" '
    /^UPDATE_CHANNEL=/ { if (!done) { print "UPDATE_CHANNEL=" channel; done = 1 }; next }
    { print }
    END { if (!done) print "UPDATE_CHANNEL=" channel }')
  log "UPDATE_CHANNEL=$CHANNEL written to $ENV_FILE (backup: $backup)"
}

# The bootstrap keys live in secrets/secrets.env, which Compose reads after .env (ADR 0042 §9):
# a key in .env moves there with its value; a missing one is generated, on a new installation
# all three, on an existing one outside production only SESSION_SECRET (the others were made in
# the data volume, and a new one would break booking links or stored secrets); a key read from
# a file (KEY_FILE in .env) is left alone. Nothing in the file is ever overwritten, and a key
# with two different values stops the installation: which one is right needs a person.
bootstrap_keys() {
  local fresh=$1 key in_env in_file conflicts=() moved=() added='' backup
  for key in "${BOOTSTRAP_KEYS[@]}"; do
    in_env=$(value "$ENV_FILE" "$key")
    in_file=$(value "$SECRETS_FILE" "$key")
    if [[ -n $in_env && -n $in_file && $in_env != "$in_file" ]]; then
      conflicts+=("$key has different values in .env and secrets/secrets.env (Compose uses the latter)")
    elif [[ -n $in_env ]]; then
      moved+=("$key")
      [[ -n $in_file ]] || added+=$(put "$key" "$in_env")$'\n'
    elif [[ -z $in_file && -z $(value "$ENV_FILE" "${key}_FILE") ]] \
      && { ((fresh)) || [[ $MODE == production || $key == SESSION_SECRET ]]; }; then
      added+=$(put "$key" "$(secret)")$'\n'
      log "generated $key"
    fi
  done
  if ((${#conflicts[@]})); then
    die "the bootstrap keys need a person:" "${conflicts[@]/#/  - }" \
      "  keep the value the database was encrypted with, remove the other, run this script again"
  fi
  install -d -m 700 "$(dirname "$SECRETS_FILE")"
  chmod 700 "$(dirname "$SECRETS_FILE")"
  if [[ -n $added ]]; then
    {
      if [[ -f $SECRETS_FILE ]]; then
        cat "$SECRETS_FILE"
      else
        printf '# Written by scripts/install.sh (ADR 0042), as "python -m app.cli init" would.\n'
        printf '# Keep a copy apart from the database backup: without SETTINGS_ENCRYPTION_KEY the\n'
        printf '# secrets stored in the database cannot be read. Never commit this file.\n'
      fi
      printf '%s' "$added"
    } | write_private "$SECRETS_FILE"
  fi
  [[ ! -f $SECRETS_FILE ]] || chmod 600 "$SECRETS_FILE"
  if ((${#moved[@]})); then
    # shellcheck disable=SC2016 # an awk program
    backup=$(rewrite_env -v keys="${moved[*]}" 'BEGIN { split(keys, k, " "); for (i in k) drop[k[i]] = 1 }
      !(substr($0, 1, index($0, "=") - 1) in drop)')
    log "moved ${moved[*]} from .env to secrets/secrets.env (backup: $backup)"
  fi
  [[ -z $added && ${#moved[@]} == 0 ]] || log "back up secrets/secrets.env apart from the database backup"
}

# The rules the application enforces at start-up, checked before anything starts.
check_env() {
  local problems=() duplicates environment
  duplicates=$(env_keys "$ENV_FILE" | sort | uniq -d | tr '\n' ' ')
  [[ -z $duplicates ]] || problems+=("set more than once: ${duplicates% } (only the last one counts)")
  environment=$(value "$ENV_FILE" ENVIRONMENT)
  if [[ $MODE == production ]]; then
    [[ ${environment:-prod} == prod ]] || problems+=("ENVIRONMENT=$environment, production needs prod")
    [[ $(value "$ENV_FILE" PUBLIC_BASE_URL) == https://* ]] || problems+=("PUBLIC_BASE_URL must start with https://")
    for key in "${BOOTSTRAP_KEYS[@]}"; do
      [[ -n $(value "$SECRETS_FILE" "$key") || -n $(value "$ENV_FILE" "${key}_FILE") ]] \
        || problems+=("$key is missing")
    done
    [[ $(value "$ENV_FILE" SIMULATOR_ENABLED) != true ]] || problems+=("SIMULATOR_ENABLED=true is refused in production")
    [[ ${CHANNEL} == release && $(value "$ENV_FILE" UPDATE_CHANNEL) != rc ]] || problems+=("UPDATE_CHANNEL=rc is refused in production (ADR 0059)")
    [[ $(value "$ENV_FILE" UPDATE_AUTO_APPLY) != true ]] || problems+=("UPDATE_AUTO_APPLY=true is refused in production (ADR 0059)")
    case $(value "$ENV_FILE" OIDC_DISCOVERY_URL) in
      '') ;;
      https://*)
        [[ -n $(value "$ENV_FILE" OIDC_CLIENT_SECRET) ]] || problems+=("OIDC_CLIENT_SECRET is missing")
        ;;
      *) problems+=("OIDC_DISCOVERY_URL must start with https://") ;;
    esac
  elif [[ $environment == prod ]]; then
    problems+=("ENVIRONMENT=prod in $MODE mode: use --mode production")
  fi
  [[ -n $(value "$ENV_FILE" UPDATE_REPOSITORY) ]] \
    || warn "UPDATE_REPOSITORY is not set: the portal will not offer updates"
  if ((${#problems[@]})); then
    die "$ENV_FILE needs changes:" "${problems[@]/#/  - }"
  fi
}

settings() {
  if [[ $MODE == production ]]; then
    if [[ -f $PROXY_ENV ]]; then
      TLS_DOMAIN=$(value "$PROXY_ENV" TLS_DOMAIN)
    else
      ask TLS_DOMAIN "Host name for HTTPS (DNS must point at this host)" ''
    fi
  fi
  local fresh=0
  if [[ -f $ENV_FILE ]]; then
    log "keeping $ENV_FILE; checking it"
    repair_env
  else
    new_env
    fresh=1
  fi
  bootstrap_keys "$fresh"
  check_env
  [[ $MODE == production ]] || return 0
  if [[ ! -f $PROXY_ENV ]]; then
    ask ACME_EMAIL "Operations mailbox for the certificate authority" ''
    log "writing $PROXY_ENV"
    {
      printf '# Written by scripts/install.sh. Only the HTTPS proxy reads this file (ADR 0030).\n'
      put TLS_DOMAIN "$TLS_DOMAIN"
      put ACME_EMAIL "$ACME_EMAIL"
      ((!ACME_STAGING)) || put ACME_CA https://acme-staging-v02.api.letsencrypt.org/directory
    } | write_private "$PROXY_ENV"
  fi
  [[ -n $TLS_DOMAIN && -n $(value "$PROXY_ENV" ACME_EMAIL) ]] \
    || die "$PROXY_ENV needs TLS_DOMAIN and ACME_EMAIL"
  [[ $(value "$ENV_FILE" PUBLIC_BASE_URL) == "https://$TLS_DOMAIN"* ]] \
    || warn "PUBLIC_BASE_URL does not match TLS_DOMAIN=$TLS_DOMAIN"
  getent hosts "$TLS_DOMAIN" >/dev/null 2>&1 || warn "$TLS_DOMAIN does not resolve yet; the certificate will wait for DNS"
}

# 4. The pinned release -------------------------------------------------------------------------

image_name() { printf 'ghcr.io/%s' "$(printf '%s' "$REPOSITORY" | tr '[:upper:]' '[:lower:]')"; }
# What the host's cosign accepts (ADR 0006): images signed by main.yml for a release tag. On
# the rc channel also those signed on main, where release candidates are built (ADR 0059).
identity_regexp() {
  local ref='tags/v[0-9]+\.[0-9]+\.[0-9]+' # through %s: single backslashes here
  [[ $CHANNEL == release ]] || ref="(heads/main|$ref)"
  printf '^https://github\\.com/%s/\\.github/workflows/main\\.yml@refs/%s$' "${REPOSITORY//./\\.}" "$ref"
}

latest_version() {
  local token tag url="https://api.github.com/repos/$REPOSITORY/releases/latest"
  # The rc channel takes the newest release or pre-release (GitHub lists newest first).
  [[ $CHANNEL == release ]] || url="https://api.github.com/repos/$REPOSITORY/releases?per_page=1"
  token=$(value "$ENV_FILE" UPDATE_CHECK_TOKEN)
  # The token goes in through a file descriptor, never on the command line (visible in ps).
  tag=$(curl -fsS -H "Accept: application/vnd.github+json" -H @<(printf 'Authorization: Bearer %s\n' "$token") \
    "$url" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -n 1) || true
  [[ $tag =~ $VERSION_RE ]] \
    || die "cannot find the latest release of $REPOSITORY: pass --version, or set UPDATE_CHECK_TOKEN for a private repository"
  printf '%s' "$tag"
}

pin_release() {
  local image digest profiles='' current
  image=$(image_name)
  case $MODE in
    production) profiles=tls ;;
    lab) profiles=dev ;;
  esac
  current=$(value "$PIN_FILE" OPENPLANNER_IMAGE)
  if [[ -z $VERSION && -n $current ]]; then
    log "keeping the pinned release $current"
    digest=$current
  else
    VERSION=${VERSION:-$(latest_version)}
    log "pulling $image:$VERSION"
    docker pull -q "$image:$VERSION" >/dev/null 2>&1 \
      || die "cannot pull $image:$VERSION; for a private package run: docker login ghcr.io -u <github user> (token with read:packages)"
    digest=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$image:$VERSION" \
      | grep -m1 "^$image@sha256:") || die "no registry digest for $image:$VERSION"
    cosign verify "$digest" --certificate-identity-regexp "$(identity_regexp)" \
      --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1 \
      || die "$digest is not signed by a release of $REPOSITORY; nothing was changed"
    log "signature verified: $digest"
  fi
  {
    printf '# Written by scripts/install.sh; the host updater replaces OPENPLANNER_IMAGE.\n'
    printf 'OPENPLANNER_MODE=%s\n' "$MODE"
    [[ -z $profiles ]] || printf 'COMPOSE_PROFILES=%s\n' "$profiles"
    printf 'OPENPLANNER_IMAGE=%s\n' "$digest"
  } >"$PIN_FILE.tmp"
  chmod 644 "$PIN_FILE.tmp"
  mv "$PIN_FILE.tmp" "$PIN_FILE"
}

# 5. Start --------------------------------------------------------------------------------------

healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) worker
  while ((SECONDS < deadline)); do
    worker=$(compose ps -q worker)
    if curl -fsS -o /dev/null "$READY_URL" \
      && [[ -n $worker && $(docker inspect -f '{{.State.Health.Status}}' "$worker") == healthy ]]; then
      return 0
    fi
    sleep 5
  done
  return 1
}

start() {
  log "starting (docker compose --env-file .env.update up -d)"
  # Only production runs the HTTPS proxy; one started by hand elsewhere restarts for ever.
  [[ $MODE == production ]] || compose --profile tls rm --stop --force proxy >/dev/null 2>&1 || true
  compose up -d
  healthy || die "not healthy within ${HEALTH_TIMEOUT}s; see: docker compose --env-file .env.update logs web worker"
  log "web and worker are healthy"
}

# 6. Host updater -------------------------------------------------------------------------------

root_install() { # root_install MODE SOURCE TARGET, creating the target's directory
  sudo install -d -m 755 "$(dirname "$3")"
  sudo install -m "$1" "$2" "$3"
}

root_file() { # root_file MODE TARGET: content on stdin
  local tmp
  tmp=$(mktemp)
  cat >"$tmp"
  root_install "$1" "$tmp" "$2"
  rm -f "$tmp"
}

install_updater() {
  local source=$DIR/scripts/updater docker_config=${DOCKER_CONFIG:-$HOME/.docker}/config.json
  log "installing the host updater (sudo)"
  root_install 755 "$source/openplanner-update.sh" "$ROOT/usr/local/sbin/openplanner-update"
  root_install 644 "$source/openplanner-updater.service" "$UNIT_DIR/openplanner-updater.service"
  root_install 644 "$source/openplanner-updater.timer" "$UNIT_DIR/openplanner-updater.timer"
  root_file 600 "$ROOT/etc/openplanner/updater.env" <<EOF
# Written by scripts/install.sh; run it again instead of editing (docs/install.md).
COMPOSE_DIR=$DIR
UPDATE_IMAGE=$(image_name)
CERT_IDENTITY_REGEXP='$(identity_regexp)'
HEALTH_TIMEOUT=$HEALTH_TIMEOUT
READY_URL=$READY_URL
EOF
  # The unit hides /home (ProtectHome=yes); open this directory only, read-only elsewhere. HOME
  # holds the registry login and cosign's cache.
  root_file 644 "$UNIT_DIR/openplanner-updater.service.d/override.conf" <<EOF
# Written by scripts/install.sh for $DIR.
[Service]
ProtectHome=read-only
ReadWritePaths=$DIR
Environment=HOME=$UPDATER_STATE
EOF
  if [[ -f $docker_config ]]; then
    grep -q '"credsStore"' "$docker_config" \
      && warn "$docker_config uses a credential helper; root cannot pull private images until it logs in to ghcr.io itself"
    sudo install -d -m 700 "$ROOT$UPDATER_STATE" "$ROOT$UPDATER_STATE/.docker"
    sudo install -m 600 "$docker_config" "$ROOT$UPDATER_STATE/.docker/config.json"
  else
    warn "no Docker login found: fine for a public image; for a private one run docker login ghcr.io and this script again"
  fi
  sudo systemd-analyze verify "$UNIT_DIR/openplanner-updater.service"
  sudo systemctl daemon-reload
  sudo systemctl enable --now openplanner-updater.timer
  log "host updater active: sudo journalctl -u openplanner-updater"
}

main() {
  parse_args "$@"
  ensure_packages
  preflight
  settings
  pin_release
  start
  if ((UPDATER)); then
    install_updater
  else
    warn "--no-updater: updates requested in the portal will wait"
  fi
  log "installed in $DIR ($MODE): $(value "$ENV_FILE" PUBLIC_BASE_URL)"
  log "every compose command here: docker compose --env-file .env.update <command>"
}

main "$@"
