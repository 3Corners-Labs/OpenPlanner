#!/usr/bin/env bash
# OpenPlanner host installer (docs/install.md). Run as the operator account from the checkout;
# the checkout is the installation directory (by convention /home/<user>/openplanner). It asks
# for sudo only to install the host updater (ADR 0029).
#
#   ./scripts/install.sh --mode production|test|lab [--version X.Y.Z] [--repository owner/name]
#                        [--non-interactive] [--no-tls | --tls-domain HOST] [--own-certificate | --internal-ca]
#                        [--idp bundled|own] [--acme-staging] [--no-updater] [--upgrade-docker] [--no-packages]
#                        [--configuration FILE [--passphrase-file PATH]]
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
#                      config.env: with --configuration (ADR 0089 point 8) the login of the
#                      package (OIDC_*), read by a one-off container of the pinned image before
#                      .env is asked for; so step 4 then runs first, and the login questions
#                      are left out. Compose reads it before .env, so .env wins.
#   3. proxy.env       with the HTTPS proxy (production and test by default, a lab with
#                      --tls-domain; ADR 0064): TLS_DOMAIN and ACME_EMAIL, or with
#                      --own-certificate the files in certs/; same rules; with --internal-ca
#                      TLS_INTERNAL=true instead (Caddy's own authority, for a lab or a network
#                      without a public name; ADR 0112)
#      the identity provider (ADR 0106 step 5): by default the bundled Keycloak in every
#                      mode (--idp bundled): the realm is rendered with generated secrets
#                      (scripts/render-keycloak-realm.sh), the proxy serves it under /idp, and its
#                      mail server is set at the first start. Every hop reaches it, and it its
#                      database, at a fixed address (ADR 0125 step 2); a host that reached it by
#                      the name idp is moved there, and both client secrets are replaced in the
#                      files and, after the start, in Keycloak. --idp own asks for the login of
#                      your own provider, as before, and on a host that ran the bundled one removes
#                      its login (secrets/keycloak-app.env), its /idp route and its containers
#                      (ADR 0125; volumes stay). Production runs Keycloak in production
#                      mode on its own PostgreSQL container (idp-db, ADR 0106 point 4), and a
#                      host that already has a login in .env keeps it
#   4. .env.update     pull <image>:<version> -> digest -> cosign verify -> pin digest, profiles
#                      and mode; with the HTTPS proxy the same for <image>-proxy:<version>
#                      (ADR 0064 step 2: no host builds Caddy); without --version existing
#                      pins stay
#   5. start           docker compose --env-file .env.update up -d, wait for health
#                      with --configuration: then the file is copied into web and applied
#                      (every organisation, group, channel, provider and integration). A JSON
#                      document (ADR 0099) is planned first and applied with the hash of that
#                      plan; an old SQLite package (ADR 0089) is converted to a replacing
#                      document and applied. A refusal (a rule, a right, another revision, a
#                      wrong passphrase) stops the script
#   6. host updater    /usr/local/sbin, systemd units, /etc/openplanner/updater.env pointing at
#                      this directory, a drop-in that opens only this directory under /home
#
# Running it again is safe: it repairs what is missing and changes nothing that is in order.
# Non-interactive runs take every answer from environment variables of the same name as the
# setting (PUBLIC_BASE_URL, TLS_DOMAIN, OIDC_CLIENT_SECRET, ...), and the configuration file's two
# paths from OPENPLANNER_CONFIGURATION and OPENPLANNER_PASSPHRASE_FILE. A document needs the
# passphrase only when it holds sealed secrets, a SQLite package always.
set -Eeuo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
# Staging root for the files outside $DIR; only the tests set it.
ROOT=${INSTALL_ROOT:-}
ENV_FILE=$DIR/.env
PROXY_ENV=$DIR/proxy.env
PIN_FILE=$DIR/.env.update
SECRETS_FILE=$DIR/secrets/secrets.env
# The login from a configuration package (ADR 0089 point 8); Compose reads it before .env.
CONFIG_ENV=$DIR/secrets/config.env
# ADR 0125 step 2: the proxy reaches the bundled identity provider at its fixed address on the
# edge network (docker-compose.yml), never by the name idp, which this host's DNS could answer
# while the container is down. scripts/render-keycloak-realm.sh and app/domain/web_transport.py
# hold the same address; a test keeps them equal.
BUNDLED_IDP_UPSTREAM=172.31.240.4:8080
# Written before the bundled provider's client secrets are replaced, removed once Keycloak has
# the new ones (new_client_secrets, idp_client_secrets): a run that stops in between leaves it,
# and the next run finishes the job with the same secrets instead of making a second pair.
ROTATION_MARK=$DIR/secrets/keycloak-secrets.pending
# Where the package or document is copied for the web container, and its name inside the container.
PACKAGE_IN_WEB=/srv/data/configuration-package.sqlite
DOCUMENT_IN_WEB=/srv/data/configuration-document.json
# The keys `python -m app.cli init` generates (app/adapters/bootstrap.py); a test keeps them equal.
BOOTSTRAP_KEYS=(SETTINGS_ENCRYPTION_KEY MANAGEMENT_TOKEN_KEY SESSION_SECRET)
UPDATER_STATE=/var/lib/openplanner-updater
UNIT_DIR=$ROOT/etc/systemd/system
OIDC_ISSUER=https://token.actions.githubusercontent.com
READY_URL=http://127.0.0.1:8000/readyz
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
# not a pin: the oldest Compose that reads env_file with path/required; packages stay newest
COMPOSE_MIN=2.24.0
# The kernel's IPv6 settings, missing when it was booted with ipv6.disable=1 (tests point it
# elsewhere). Such a kernel cannot make the dual-stack edge network of docker-compose.yml.
KERNEL_IPV6=${KERNEL_IPV6:-/proc/sys/net/ipv6}
VERSION_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}(-rc\.[0-9]{1,4})?$'
RELEASE_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
REPOSITORY_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'

OS_RELEASE=$ROOT/etc/os-release
# Docker's release key (https://docs.docker.com/engine/install/ubuntu/); a fixed trust anchor.
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
DOCKER_KEYRING=$ROOT/etc/apt/keyrings/docker.asc

MODE='' VERSION='' REPOSITORY='' INTERACTIVE=1 ACME_STAGING=0 UPDATER=1 PACKAGES=1 UPGRADE_DOCKER=0
# TLS: '' decides by the pin file, then the mode; 1 runs the HTTPS proxy, 0 leaves TLS to your own.
TLS='' OWN_CERT=0 INTERNAL_CA=0
# ADR 0106 step 5: bundled | own; empty decides by the pin file, then the mode.
IDP=''
CERTS_DIR=$DIR/certs
# --configuration FILE --passphrase-file PATH (ADR 0089 point 8, ADR 0099); PACKAGE_DIR is the
# readable copy made for the containers, removed on exit. CONFIG_KIND is what FILE is, found by
# its content: sqlite (an ADR 0089 package) or document (JSON, ADR 0099).
CONFIGURATION=${OPENPLANNER_CONFIGURATION:-} PASSPHRASE_FILE=${OPENPLANNER_PASSPHRASE_FILE:-} PACKAGE_DIR=''
CONFIG_KIND=''
OWN_CERT_FILE=/certs/fullchain.pem OWN_KEY_FILE=/certs/privkey.pem

log() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$1" >&2
  shift
  (($# == 0)) || printf '%s\n' "$@" >&2
  exit 1
}
usage() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
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
      --channel) die "--channel is gone (ADR 0067): the superuser chooses the channel on /admin/updates" ;;
      --non-interactive) INTERACTIVE=0 ;;
      --acme-staging) ACME_STAGING=1 ;;
      --no-tls) TLS=0 ;;
      --tls-domain) TLS=1 TLS_DOMAIN=${2:?--tls-domain needs a host name} && shift ;;
      --own-certificate) OWN_CERT=1 ;;
      --internal-ca) INTERNAL_CA=1 ;;
      --idp) IDP=${2:?--idp needs bundled or own} && shift ;;
      --no-updater) UPDATER=0 ;;
      --no-packages) PACKAGES=0 ;;
      --upgrade-docker) UPGRADE_DOCKER=1 ;;
      --configuration) CONFIGURATION=${2:?--configuration needs a file} && shift ;;
      --passphrase-file) PASSPHRASE_FILE=${2:?--passphrase-file needs a path} && shift ;;
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
  # ADR 0067: a release candidate may be pinned on a test or lab host; production runs releases.
  [[ -z $VERSION || $VERSION =~ $VERSION_RE ]] || die "--version must look like 1.2.3 (or 1.2.3-rc.4 outside production)"
  [[ -z $VERSION || $MODE != production || $VERSION =~ $RELEASE_RE ]] \
    || die "--version $VERSION is a release candidate; production runs releases only"
  # ADR 0064: HTTPS is the standard shape. A rerun remembers the choice through the pin file.
  TLS=${TLS:-$(value "$PIN_FILE" OPENPLANNER_TLS)}
  if [[ -z $TLS ]]; then
    if [[ $MODE == lab ]]; then TLS=0; else TLS=1; fi
  fi
  ((TLS)) || ((!OWN_CERT)) || die "--own-certificate needs the HTTPS proxy; drop --no-tls"
  ((TLS)) || ((!INTERNAL_CA)) || die "--internal-ca needs the HTTPS proxy; drop --no-tls"
  ((!OWN_CERT)) || ((!INTERNAL_CA)) || die "--own-certificate and --internal-ca exclude each other"
  [[ $MODE != production ]] || ((!INTERNAL_CA)) || warn "--internal-ca in production: browsers will not trust the certificate until its root is installed on every machine; use ACME or --own-certificate"
  # ADR 0106 point 1: the bundled provider is the default in every mode. A production host that
  # already has a login of its own (in .env, or named in the environment of this run) keeps it,
  # until --idp bundled says otherwise.
  IDP=${IDP:-$(value "$PIN_FILE" OPENPLANNER_IDP)}
  if [[ -z $IDP ]]; then
    if [[ $MODE == production && -n ${OIDC_DISCOVERY_URL:-$(value "$ENV_FILE" OIDC_DISCOVERY_URL)} ]]; then IDP=own; else IDP=bundled; fi
  fi
  case $IDP in bundled | own) ;; *) die "--idp must be bundled or own, not '$IDP'" ;; esac
  REPOSITORY=${REPOSITORY:-$(value "$ENV_FILE" UPDATE_REPOSITORY)}
  REPOSITORY=${REPOSITORY:-3Corners-Labs/OpenPlanner}
  [[ $REPOSITORY =~ $REPOSITORY_RE ]] || die "--repository must be owner/name"
  # ADR 0089 point 8: a package is read with its passphrase, which never travels on a command
  # line (ps, shell history) or in this script's output; it comes from a file, like the CLI's.
  # A JSON document (ADR 0099) needs one only when it holds sealed secrets.
  [[ -z $CONFIGURATION && -n $PASSPHRASE_FILE ]] \
    && die "--configuration and --passphrase-file go together (or OPENPLANNER_CONFIGURATION and OPENPLANNER_PASSPHRASE_FILE)"
  [[ -z $CONFIGURATION ]] || [[ -f $CONFIGURATION && -r $CONFIGURATION ]] \
    || die "cannot read the configuration file $CONFIGURATION"
  [[ -z $PASSPHRASE_FILE ]] || [[ -f $PASSPHRASE_FILE && -r $PASSPHRASE_FILE ]] \
    || die "cannot read the passphrase file $PASSPHRASE_FILE (its first line is the passphrase)"
  [[ -z $CONFIGURATION ]] || config_kind
  if [[ $CONFIG_KIND == sqlite && -z $PASSPHRASE_FILE ]]; then
    die "--configuration and --passphrase-file go together (a SQLite package is opened with its passphrase)"
  fi
  if [[ $CONFIG_KIND == document && -z $PASSPHRASE_FILE ]] && grep -q '"sealed"' "$CONFIGURATION"; then
    die "the configuration document holds sealed secrets: give --passphrase-file (its first line is the passphrase)"
  fi
}

# What --configuration names, by its content: a SQLite package starts with the SQLite header
# (ADR 0089), a document is JSON, so its first character that is not white space is a brace.
config_kind() {
  local start
  if [[ $(head -c 15 "$CONFIGURATION") == 'SQLite format 3' ]]; then
    CONFIG_KIND=sqlite
    return 0
  fi
  start=$(head -c 4096 "$CONFIGURATION")
  [[ $start =~ ^[[:space:]]*\{ ]] \
    || die "$CONFIGURATION is neither a configuration document (JSON) nor a SQLite configuration package"
  CONFIG_KIND=document
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
  ipv6_forwarding
}

# Visitors over IPv6 keep their address only where Docker forwards them to the proxy with NAT
# (ip6tables, on by default from Docker Engine 27; network edge in docker-compose.yml). Without it
# the host still works and they share one address in the rate limits, so this only warns.
ipv6_forwarding() {
  local engine
  if [[ ! -d $KERNEL_IPV6 ]]; then
    log "the kernel has no IPv6 (ipv6.disable=1): the edge network stays IPv4 only"
    return 0
  fi
  ((TLS)) || return 0
  engine=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || return 0
  if ! version_at_least "$engine" 27.0.0; then
    warn "Docker Engine $engine forwards visitors over IPv6 without NAT, so they share one address in the rate limits; run again with --upgrade-docker"
  elif grep -Eq '"ip6tables"[[:space:]]*:[[:space:]]*false' "$ROOT/etc/docker/daemon.json" 2>/dev/null; then
    warn "$ROOT/etc/docker/daemon.json switches ip6tables off, so visitors over IPv6 share one address in the rate limits; remove that line and restart Docker"
  fi
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

# public_address HINT DEFAULT: with the HTTPS proxy the address follows the host name.
public_address() {
  if ((TLS)); then
    PUBLIC_BASE_URL=https://$TLS_DOMAIN
  else
    ask PUBLIC_BASE_URL "Public address of this installation${1:+ ($1)}" "$2"
  fi
}

new_env() {
  local environment=test smtp_default='' oidc_default=''
  log "writing $ENV_FILE"
  case $MODE in
    production)
      environment=prod
      public_address "https (behind your proxy)" ''
      ;;
    test) public_address "" "http://$(hostname -f 2>/dev/null || hostname):8000" ;;
    lab)
      public_address "" "http://127.0.0.1:8000"
      smtp_default=mailpit
      SMTP_PORT=${SMTP_PORT:-1025} SMTP_STARTTLS=${SMTP_STARTTLS:-false}
      ;;
  esac
  # Behind a proxy of your own, rate limits must see the visitor's address (ADR 0030 §6).
  ((TLS)) || [[ $MODE == lab ]] \
    || ask FORWARDED_ALLOW_IPS "Address of your reverse proxy, for X-Forwarded-For (empty: none)" '' optional
  # The login: from the package's file when it holds one (ADR 0089 point 8), else asked.
  if [[ $IDP == bundled ]]; then
    log "the login (OIDC_*) comes from the bundled identity provider (secrets/keycloak-app.env); not asked"
    OIDC_DISCOVERY_URL='' OIDC_CLIENT_ID='' OIDC_CLIENT_SECRET=''
  elif [[ -n $(value "$CONFIG_ENV" OIDC_DISCOVERY_URL) ]]; then
    log "the login (OIDC_*) comes from the configuration package; not asked"
    OIDC_DISCOVERY_URL='' OIDC_CLIENT_ID='' OIDC_CLIENT_SECRET=''
  else
    ask OIDC_DISCOVERY_URL "OIDC discovery URL of the identity provider (empty: no admin portal)" "$oidc_default" optional
    if [[ -n $OIDC_DISCOVERY_URL ]]; then
      ask OIDC_CLIENT_ID "OIDC client ID" openplanner-admin
      ask OIDC_CLIENT_SECRET "OIDC client secret" '' secret
    fi
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
    # ADR 0106 point 4: with the bundled provider the second factor is asked for, in every mode.
    [[ $IDP != bundled ]] || put ADMIN_MFA_REQUIRED true
    put PUBLIC_BASE_URL "$PUBLIC_BASE_URL"
    put FORWARDED_ALLOW_IPS "${FORWARDED_ALLOW_IPS:-}"
    [[ -z $OIDC_DISCOVERY_URL ]] && [[ -f $CONFIG_ENV ]] && printf '# The login (OIDC_*): secrets/config.env, from the configuration package; a value here wins.\n'
    put OIDC_DISCOVERY_URL "$OIDC_DISCOVERY_URL"
    [[ -z ${IDP_ISSUER:-} ]] || put OIDC_ISSUER "$IDP_ISSUER"
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
    printf '# Webex and WxCC: docs/runbook.md, "Webex meetings and Webex Contact Center".\n'
  } | write_private "$ENV_FILE"
}

env_keys() { sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$1"; }
# login_value KEY: as Compose sees it, .env over secrets/config.env (ADR 0089 point 8).
login_value() {
  local found
  found=$(value "$ENV_FILE" "$1")
  [[ -n $found ]] || found=$(value "$CONFIG_ENV" "$1")
  printf '%s' "$found"
}

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
    [[ $(value "$ENV_FILE" UPDATE_CHANNEL) != rc ]] || problems+=("UPDATE_CHANNEL=rc is refused in production (ADR 0067)")
    [[ $(value "$ENV_FILE" UPDATE_AUTO_APPLY) != true ]] || problems+=("UPDATE_AUTO_APPLY=true is refused in production (ADR 0067)")
    case $(login_value OIDC_DISCOVERY_URL) in
      '') ;;
      https://*)
        [[ -n $(login_value OIDC_CLIENT_SECRET) ]] || problems+=("OIDC_CLIENT_SECRET is missing")
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

# 2a. The configuration package (ADR 0089 point 8) ----------------------------------------------

# The package is read by the application itself (the pinned image, so step 4 has run). The web
# container runs as uid 10001 with a read-only filesystem, so it gets a copy it can read in a
# directory of its own (a package without its passphrase holds no secret in clear text, ADR
# 0089 point 3) and prints the login to standard output; the passphrase goes in on standard
# input, never on the command line.
package_copy() {
  PACKAGE_DIR=$(mktemp -d "$DIR/.package.XXXXXX")
  chmod 755 "$PACKAGE_DIR"
  install -m 644 "$CONFIGURATION" "$PACKAGE_DIR/package.sqlite"
  trap 'rm -rf "$PACKAGE_DIR"' EXIT
}

# The document goes into web from a readable copy too: `docker compose cp` keeps the owner and
# the mode of the operator's file (a 600 document the web user cannot read otherwise).
document_copy() {
  PACKAGE_DIR=$(mktemp -d "$DIR/.package.XXXXXX")
  chmod 755 "$PACKAGE_DIR"
  install -m 644 "$CONFIGURATION" "$PACKAGE_DIR/document.json"
  trap 'rm -rf "$PACKAGE_DIR"' EXIT
}

package_env() {
  local lines line key val
  log "reading the login from the configuration package $CONFIGURATION"
  if ! lines=$(compose run --rm --no-deps -T -v "$PACKAGE_DIR:/package:ro" web \
    python -m app.cli config env /package/package.sqlite --passphrase-file /dev/stdin --out - \
    <"$PASSPHRASE_FILE"); then
    die "cannot read the configuration package:" "${lines:-  (no output; see docker compose logs)}"
  fi
  # Quoted as .env is (put), so Compose takes the values literally; checked before writing.
  local content=''
  while IFS= read -r line; do
    [[ $line == *=* ]] || continue
    key=${line%%=*} val=${line#*=}
    [[ $val != *"'"* ]] || die "the package's $key contains a quote, which .env files cannot hold"
    content+=$(put "$key" "$val")$'\n'
  done <<<"$lines"
  install -d -m 700 "$(dirname "$CONFIG_ENV")"
  {
    printf '# Written by scripts/install.sh from a configuration package (ADR 0089). Holds the\n'
    printf '# login (OIDC_*): mode 600. Compose reads it before .env, so a value in .env wins.\n'
    printf '%s' "$content"
  } | write_private "$CONFIG_ENV"
  if [[ -n $(value "$CONFIG_ENV" OIDC_DISCOVERY_URL) ]]; then
    log "login written to $CONFIG_ENV"
  else
    log "the package holds no login; the identity provider is asked for (or taken from the environment)"
  fi
}

# 5a. After health: the package into web, imported, the copy removed whatever the outcome.
import_package() {
  local report code=0
  log "importing the configuration package"
  compose cp "$PACKAGE_DIR/package.sqlite" "web:$PACKAGE_IN_WEB"
  report=$(compose exec -T web python -m app.cli config import "$PACKAGE_IN_WEB" \
    --passphrase-file /dev/stdin <"$PASSPHRASE_FILE") || code=$?
  compose exec -T web rm -f "$PACKAGE_IN_WEB" || warn "could not remove $PACKAGE_IN_WEB from the web container"
  if ((code)); then
    case $report in
      *configuration_package_version*) die "the configuration package was refused: it was exported from another version of OpenPlanner; export it again from an installation on this version" "  $report" ;;
      *configuration_passphrase_wrong*) die "the configuration package was refused: wrong passphrase" ;;
      *not_permitted* | *configuration_object_in_use* | *discovery_refused* | *integration_not_ready*)
        die "the configuration package was refused by the rules of the configuration document it converts to (ADR 0099 point 14)" "  $report" ;;
      *) die "the configuration package was refused (exit $code)" "${report:+  $report}" ;;
    esac
  fi
  printf '%s\n' "$report" | sed 's/^/    /'
  log "configuration imported; web and worker reload the settings within ten seconds"
}

# 5b. After health: a configuration document (ADR 0099) into web, planned, then applied with the
# hash of that plan, the copy removed whatever the outcome. The passphrase, when the document
# holds sealed secrets, goes in on standard input, never on the command line.
document_cli() {
  if [[ -n $PASSPHRASE_FILE ]]; then
    compose exec -T web python -m app.cli config "$@" --passphrase-file /dev/stdin <"$PASSPHRASE_FILE"
  else
    compose exec -T web python -m app.cli config "$@" </dev/null
  fi
}

apply_document() {
  local plan report hash deletes code=0
  log "planning the configuration document"
  compose cp "$PACKAGE_DIR/document.json" "web:$DOCUMENT_IN_WEB"
  plan=$(document_cli plan "$DOCUMENT_IN_WEB") || code=$?
  if ((code)); then
    compose exec -T web rm -f "$DOCUMENT_IN_WEB" || warn "could not remove $DOCUMENT_IN_WEB from the web container"
    case $plan in
      *configuration_passphrase_wrong*) die "the configuration document was refused: wrong passphrase" ;;
      *configuration_passphrase_short*) die "the configuration document holds sealed secrets: give --passphrase-file" ;;
      *) die "the configuration document was refused; nothing was applied (exit $code)" "${plan:+$(printf '%s\n' "$plan" | sed 's/^/    /')}" ;;
    esac
  fi
  printf '%s\n' "$plan" | sed 's/^/    /'
  deletes=$(grep -c '^delete ' <<<"$plan" || true)
  if ((deletes)); then
    warn "the plan deletes $deletes object(s); on an installation in use only what nothing refers to is deleted"
  fi
  hash=$(sed -n 's/^plan hash //p' <<<"$plan")
  if [[ ! $hash =~ ^[0-9a-f]{64}$ ]]; then
    compose exec -T web rm -f "$DOCUMENT_IN_WEB" || warn "could not remove $DOCUMENT_IN_WEB from the web container"
    die "the plan of the configuration document has no hash; nothing was applied"
  fi
  log "applying the configuration document (plan $hash)"
  report=$(document_cli apply "$DOCUMENT_IN_WEB" --plan-hash "$hash") || code=$?
  compose exec -T web rm -f "$DOCUMENT_IN_WEB" || warn "could not remove $DOCUMENT_IN_WEB from the web container"
  if ((code)); then
    die "the configuration document was refused; nothing was applied (exit $code)" "${report:+$(printf '%s\n' "$report" | sed 's/^/    /')}"
  fi
  printf '%s\n' "$report" | sed 's/^/    /'
  log "configuration applied; web and worker reload the settings within ten seconds"
}

settings() {
  if ((TLS)); then
    if [[ -f $PROXY_ENV ]]; then
      TLS_DOMAIN=${TLS_DOMAIN:-$(value "$PROXY_ENV" TLS_DOMAIN)}
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
  render_idp
  ((TLS)) || return 0
  proxy_settings
}

# ADR 0106 step 5: the bundled identity provider. The realm and the application's half of it
# (secrets/keycloak-app.env: the directory and the login) are rendered from the public address
# with generated secrets; a changed public address replaces the one the earlier run kept.
render_idp() {
  if [[ $IDP != bundled ]]; then
    retire_bundled_login
    return 0
  fi
  local address database=h2 kept=$DIR/secrets/keycloak.env
  # Production: Keycloak's production mode on its own PostgreSQL (ADR 0106 point 4).
  [[ $MODE != production ]] || database=postgres
  address=$(value "$ENV_FILE" PUBLIC_BASE_URL)
  if [[ -f $kept && -n $address && $(value "$kept" KC_OPENPLANNER_BASE_URL) != "${address%/}" ]]; then
    sed -i.bak "/^KC_OPENPLANNER_BASE_URL=/d; /^KC_HOSTNAME=/d" "$kept" && rm -f "$kept.bak"
  fi
  new_client_secrets "$kept"
  [[ -f $DIR/scripts/render-keycloak-realm.sh ]] || die "scripts/render-keycloak-realm.sh is missing from $DIR"
  log "rendering the bundled identity provider's realm"
  "$BASH" "$DIR/scripts/render-keycloak-realm.sh" --dir "$DIR" --database "$database" || die "the realm could not be rendered"
  ((TLS)) || warn "the bundled identity provider without the HTTPS proxy answers on this host only (127.0.0.1:8080); logging in from another machine needs --tls-domain"
  log "first password of the portal user 'super': KC_SUPER_PASSWORD in $kept (changed at the first login)"
}

# ADR 0125 step 2: a kept realm whose login was not rendered on the fixed address reached the
# provider by the name idp (or comes back from --idp own), so its two client secrets may be held by
# whatever answered that name while the container was down; the render script keeps existing
# values, so nothing else would replace them. They are dropped here, the render script makes new
# ones for the files, and idp_client_secrets sets them in Keycloak once it runs. The mark goes
# first, so a run that stops in between keeps the new pair for the next one.
new_client_secrets() {
  local kept=$1
  [[ -f $kept && ! -f $ROTATION_MARK ]] || return 0
  ! grep -q "^OIDC_DISCOVERY_URL=http://$BUNDLED_IDP_UPSTREAM/" "$DIR/secrets/keycloak-app.env" 2>/dev/null \
    || return 0
  : >"$ROTATION_MARK"
  sed -i.bak '/^KC_OPENPLANNER_CLIENT_SECRET=/d; /^KC_DIRECTORY_CLIENT_SECRET=/d' "$kept" && rm -f "$kept.bak"
  log "replacing the bundled identity provider's two client secrets: its login was not on the fixed address, so whatever answered the name idp may hold them (ADR 0125)"
}

# ADR 0125 point 6: a host that leaves the bundled provider (--idp own) drops the application's
# half of it. Compose reads secrets/keycloak-app.env after .env, so its login and directory would
# override the ones the operator chose. Nothing is lost: the render script writes the file again
# from secrets/keycloak.env when the host returns to --idp bundled, with new client secrets
# (new_client_secrets).
retire_bundled_login() {
  local app_env=$DIR/secrets/keycloak-app.env
  [[ -f $app_env ]] || return 0
  rm -f "$app_env"
  log "removed secrets/keycloak-app.env: with --idp own the login comes from .env (the bundled realm and its secrets stay in secrets/keycloak.env)"
}

# ADR 0064: the proxy's own file. ACME by default; --own-certificate names the files in certs/.
proxy_settings() {
  install -d -m 755 "$CERTS_DIR"
  if ((OWN_CERT)); then
    for file in fullchain.pem privkey.pem; do
      [[ -f $CERTS_DIR/$file ]] || die "--own-certificate: put the certificate chain and key in $CERTS_DIR as fullchain.pem and privkey.pem (readable by uid 10001)"
    done
  fi
  local write=0
  [[ -f $PROXY_ENV ]] || write=1
  ((!OWN_CERT)) || [[ -n $(value "$PROXY_ENV" TLS_CERT_FILE) ]] || write=1 # switching to own files
  ((!INTERNAL_CA)) || [[ $(value "$PROXY_ENV" TLS_INTERNAL) == true ]] || write=1 # switching to the internal CA
  if ((write)); then
    ((OWN_CERT)) || ((INTERNAL_CA)) || ask ACME_EMAIL "Operations mailbox for the certificate authority" ''
    log "writing $PROXY_ENV"
    {
      printf '# Written by scripts/install.sh. Only the HTTPS proxy reads this file (ADR 0030, ADR 0064).\n'
      put TLS_DOMAIN "$TLS_DOMAIN"
      if ((OWN_CERT)); then
        printf '# Own certificate: renewal is yours; restart the proxy after replacing the files.\n'
        put TLS_CERT_FILE "$OWN_CERT_FILE"
        put TLS_KEY_FILE "$OWN_KEY_FILE"
      elif ((INTERNAL_CA)); then
        printf "# Caddy's own certificate authority (ADR 0112): install its root once (System status).\n"
        put TLS_INTERNAL true
      else
        put ACME_EMAIL "$ACME_EMAIL"
        ((!ACME_STAGING)) || put ACME_CA https://acme-staging-v02.api.letsencrypt.org/directory
      fi
    } | write_private "$PROXY_ENV"
  fi
  # ADR 0112: the proxy serves the bundled Keycloak under /idp, at its fixed address (ADR 0125
  # step 2); a host whose route named something else, the name idp before step 2, is moved over.
  # A host that leaves it (--idp own, ADR 0125 point 6) drops the route.
  local upstream
  upstream=$(value "$PROXY_ENV" IDP_UPSTREAM)
  if [[ $IDP == bundled && $upstream != "$BUNDLED_IDP_UPSTREAM" ]]; then
    sed -i.bak '/^# The bundled identity provider, under \/idp/d; /^IDP_UPSTREAM=/d' "$PROXY_ENV" && rm -f "$PROXY_ENV.bak"
    printf '# The bundled identity provider, under /idp on this host name (ADR 0112), at its fixed address (ADR 0125).\nIDP_UPSTREAM=%s\n' "$BUNDLED_IDP_UPSTREAM" >>"$PROXY_ENV"
    [[ -z $upstream ]] || log "IDP_UPSTREAM in $PROXY_ENV: the bundled provider's fixed address $BUNDLED_IDP_UPSTREAM instead of $upstream (ADR 0125)"
  elif [[ $IDP != bundled && -n $upstream ]]; then
    sed -i.bak '/^# The bundled identity provider, under \/idp/d; /^IDP_UPSTREAM=/d' "$PROXY_ENV" && rm -f "$PROXY_ENV.bak"
    log "removed IDP_UPSTREAM from $PROXY_ENV: with --idp own the proxy serves no /idp route"
  fi
  [[ -n $TLS_DOMAIN ]] || die "$PROXY_ENV needs TLS_DOMAIN"
  [[ -n $(value "$PROXY_ENV" ACME_EMAIL) || -n $(value "$PROXY_ENV" TLS_CERT_FILE) \
    || $(value "$PROXY_ENV" TLS_INTERNAL) == true ]] \
    || die "$PROXY_ENV needs ACME_EMAIL, or TLS_CERT_FILE and TLS_KEY_FILE (--own-certificate), or TLS_INTERNAL=true (--internal-ca)"
  ((!OWN_CERT)) || lock_certificate_source
  ((!INTERNAL_CA)) || lock_certificate_source
  [[ $(value "$ENV_FILE" PUBLIC_BASE_URL) == "https://$TLS_DOMAIN"* ]] \
    || warn "PUBLIC_BASE_URL does not match TLS_DOMAIN=$TLS_DOMAIN"
  getent hosts "$TLS_DOMAIN" >/dev/null 2>&1 || warn "$TLS_DOMAIN does not resolve yet; the certificate will wait for DNS"
}

# ADR 0070: with the operator's files the proxy ignores the portal's choice, so the portal
# setting is locked to `own` in .env; the page then says the host manages the certificate.
lock_certificate_source() {
  local backup
  [[ $(value "$ENV_FILE" TLS_CERTIFICATE_SOURCE) != own ]] || return 0
  # shellcheck disable=SC2016 # an awk program
  backup=$(rewrite_env '
    /^TLS_CERTIFICATE_SOURCE=/ { if (!done) { print "TLS_CERTIFICATE_SOURCE=own"; done = 1 }; next }
    { print }
    END { if (!done) print "TLS_CERTIFICATE_SOURCE=own" }')
  log "TLS_CERTIFICATE_SOURCE=own written to $ENV_FILE: the portal shows the certificate as managed on the host (backup: $backup)"
}

# 4. The pinned release -------------------------------------------------------------------------

image_name() { printf 'ghcr.io/%s' "$(printf '%s' "$REPOSITORY" | tr '[:upper:]' '[:lower:]')"; }
# The TLS proxy, released and signed next to the application image (ADR 0064 step 2).
proxy_image_name() { printf '%s-proxy' "$(image_name)"; }
# What the host's cosign accepts (ADR 0006): images signed by main.yml for a release tag. Test
# and lab hosts also trust those signed on main, where release candidates are built; the
# installer mode is the host team's trust decision, the channel is the superuser's (ADR 0067).
identity_regexp() {
  local ref='tags/v[0-9]+\.[0-9]+\.[0-9]+' # through %s: single backslashes here
  [[ $MODE == production ]] || ref="(heads/main|$ref)"
  printf '^https://github\\.com/%s/\\.github/workflows/main\\.yml@refs/%s$' "${REPOSITORY//./\\.}" "$ref"
}

latest_version() {
  local token tag url="https://api.github.com/repos/$REPOSITORY/releases/latest"
  token=$(value "$ENV_FILE" UPDATE_CHECK_TOKEN)
  token=${token:-${UPDATE_CHECK_TOKEN:-}} # with a package, step 4 runs before .env exists
  # The token goes in through a file descriptor, never on the command line (visible in ps).
  tag=$(curl -fsS -H "Accept: application/vnd.github+json" -H @<(printf 'Authorization: Bearer %s\n' "$token") \
    "$url" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -n 1) || true
  [[ $tag =~ $VERSION_RE ]] \
    || die "cannot find the latest release of $REPOSITORY: pass --version, or set UPDATE_CHECK_TOKEN for a private repository"
  printf '%s' "$tag"
}

# pull_verified VAR IMAGE VERSION: pull the image, take its registry digest, verify the
# signature (ADR 0006) and leave the digest reference in VAR. The same for both images.
pull_verified() {
  local image=$2 version=$3 ref # not `digest`: the caller's variable of that name is the target
  log "pulling $image:$version"
  docker pull -q "$image:$version" >/dev/null 2>&1 \
    || die "cannot pull $image:$version; for a private package run: docker login ghcr.io -u <github user> (token with read:packages)"
  ref=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$image:$version" \
    | grep -m1 "^$image@sha256:") || die "no registry digest for $image:$version"
  cosign verify "$ref" --certificate-identity-regexp "$(identity_regexp)" \
    --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1 \
    || die "$ref is not signed by a release of $REPOSITORY; nothing was changed"
  log "signature verified: $ref"
  printf -v "$1" '%s' "$ref"
}

# The version baked into a pulled application image (APP_VERSION, ADR 0052), for an
# installation whose proxy image is not pinned yet (from before ADR 0064 step 2).
pinned_version() {
  docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null \
    | sed -n 's/^APP_VERSION=//p' | head -n 1
}

pin_release() {
  local image digest profiles='' current proxy_digest='' proxy_current version
  image=$(image_name)
  # idp: the bundled Keycloak (ADR 0090); mailpit: the lab's mail catcher (ADR 0106 step 5); tls:
  # the proxy (ADR 0064). `dev` is for developers only: the development realm and Mailpit.
  [[ $IDP != bundled ]] || profiles=idp
  [[ $IDP != bundled || $MODE != production ]] || profiles=idp,idp-db
  [[ $MODE != lab ]] || profiles=${profiles:+$profiles,}mailpit
  if ((TLS)); then profiles=${profiles:+$profiles,}tls; fi
  current=$(value "$PIN_FILE" OPENPLANNER_IMAGE)
  proxy_current=$(value "$PIN_FILE" OPENPLANNER_PROXY_IMAGE)
  if [[ -z $VERSION && -n $current ]]; then
    log "keeping the pinned release $current"
    digest=$current
  else
    VERSION=${VERSION:-$(latest_version)}
    pull_verified digest "$image" "$VERSION"
  fi
  # The proxy image at the same version (ADR 0064 step 2); a host never builds it.
  if ((TLS)); then
    if [[ -n $VERSION ]]; then
      pull_verified proxy_digest "$(proxy_image_name)" "$VERSION"
    elif [[ -n $proxy_current ]]; then
      log "keeping the pinned proxy image $proxy_current"
      proxy_digest=$proxy_current
    else
      version=$(pinned_version "$digest")
      [[ $version =~ $VERSION_RE ]] \
        || die "cannot tell the version of $digest, so the proxy image cannot be pinned; run again with --version"
      pull_verified proxy_digest "$(proxy_image_name)" "$version"
    fi
  fi
  {
    printf '# Written by scripts/install.sh; the host updater replaces the image pins.\n'
    printf 'OPENPLANNER_MODE=%s\n' "$MODE"
    printf 'OPENPLANNER_TLS=%s\n' "$TLS"
    printf 'OPENPLANNER_IDP=%s\n' "$IDP"
    [[ $IDP != bundled || $MODE != production ]] || printf 'OPENPLANNER_IDP_START=start\n'
    [[ -z $profiles ]] || printf 'COMPOSE_PROFILES=%s\n' "$profiles"
    [[ -d $KERNEL_IPV6 ]] || printf 'OPENPLANNER_EDGE_IPV6=false\n'
    printf 'OPENPLANNER_IMAGE=%s\n' "$digest"
    [[ -z $proxy_digest ]] || printf 'OPENPLANNER_PROXY_IMAGE=%s\n' "$proxy_digest"
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

# The edge network has IPv6 unless the kernel has none (docker-compose.yml). Compose 2.31 recreates
# a network whose settings changed; 2.29 kept the IPv4-only edge network of before without a word,
# so a stale one is removed with the stack.
edge_network() {
  local web project have wanted=true
  [[ -d $KERNEL_IPV6 ]] || wanted=false
  web=$(compose ps -a -q web 2>/dev/null) || return 0
  [[ -n $web ]] || return 0
  project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$web") || return 0
  have=$(docker network inspect -f '{{.EnableIPv6}}' "${project}_edge" 2>/dev/null) || return 0
  [[ $have != "$wanted" ]] || return 0
  log "the edge network has IPv6 $have, this host wants $wanted: recreating it (docker compose down, then up)"
  compose down
}

start() {
  log "starting (docker compose --env-file .env.update up -d)"
  # Without the tls profile a proxy started by hand earlier would restart for ever.
  ((TLS)) || compose --profile tls rm --stop --force proxy >/dev/null 2>&1 || true
  edge_network
  compose up -d
  healthy || die "not healthy within ${HEALTH_TIMEOUT}s; see: docker compose --env-file .env.update logs web worker"
  log "web and worker are healthy"
  retire_bundled_provider
  idp_client_secrets
  idp_mail
}

# ADR 0125 point 6: a host that left the bundled provider stops it, after `up -d` has replaced
# the proxy, web and worker that still used its name: stopped first, the name idp would reach
# the host's resolver while they run. Its volumes and secrets/keycloak.env stay for the way back
# (--idp bundled; `compose rm` never removes a named volume).
retire_bundled_provider() {
  [[ $IDP != bundled && -f $DIR/secrets/keycloak.env ]] || return 0
  if compose --profile idp --profile idp-db rm --stop --force idp idp-db >/dev/null 2>&1; then
    log "the bundled identity provider's containers (idp, idp-db) are removed; its volumes and secrets/keycloak.env stay"
  else
    warn "could not remove the bundled identity provider's containers; run: docker compose --env-file .env.update --profile idp --profile idp-db rm --stop --force idp idp-db"
  fi
  ((TLS)) || warn "without the HTTPS proxy, remove any /idp route your own reverse proxy has to this host"
}

# ADR 0106 step 5: the realm's mail server, for the invitations and password resets that Keycloak
# itself sends. The lab's Mailpit, else the installation's own SMTP settings; the password goes to
# the container through stdin, never through an argument.
idp_mail() {
  [[ $IDP == bundled ]] || return 0
  local host port from user password starttls json
  host=$(value "$ENV_FILE" SMTP_HOST)
  if [[ -z $host ]]; then
    warn "no SMTP_HOST in $ENV_FILE: Keycloak cannot mail invitations until the realm has a mail server (docs/runbook.md, Bundled identity provider)"
    return 0
  fi
  port=$(value "$ENV_FILE" SMTP_PORT) starttls=$(value "$ENV_FILE" SMTP_STARTTLS)
  user=$(value "$ENV_FILE" SMTP_USERNAME) password=$(value "$ENV_FILE" SMTP_PASSWORD)
  from=$(value "$ENV_FILE" MAIL_FROM)
  from=${from##*<} from=${from%%>*} # "Name <address>" → address
  from=${from:-noreply@openplanner.invalid}
  json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  json=$(printf '{"smtpServer":{"host":"%s","port":"%s","from":"%s","fromDisplayName":"OpenPlanner","starttls":"%s","ssl":"false","auth":"%s"%s}}' \
    "$(json_escape "$host")" "${port:-587}" "$(json_escape "$from")" "${starttls:-true}" \
    "$([[ -n $user ]] && echo true || echo false)" \
    "$([[ -n $user ]] && printf ',"user":"%s","password":"%s"' "$(json_escape "$user")" "$(json_escape "$password")")")
  # shellcheck disable=SC2016 # runs in the container, which expands $rc
  printf '%s' "$json" | idp_admin '
      cat >/tmp/op-smtp.json && /opt/keycloak/bin/kcadm.sh update realms/openplanner -f /tmp/op-smtp.json >/dev/null 2>&1
      rc=$?; rm -f /tmp/op-smtp.json; exit $rc' \
    || { warn "the bundled identity provider did not take its mail server within ${HEALTH_TIMEOUT}s; set it in its console (docs/runbook.md)"; return 0; }
  log "the bundled identity provider's mail server: $host (Keycloak mails invitations and resets itself)"
}

# ADR 0125 step 2: Keycloak takes the client secrets that new_client_secrets had rendered. It
# imports the realm only while the realm does not exist, so a new render never reaches a running
# one: kcadm.sh sets both, with the values on stdin. Until then staff cannot log in, so a failure
# stops the run; the mark stays, and the next run sets the same secrets.
idp_client_secrets() {
  [[ $IDP == bundled && -f $ROTATION_MARK ]] || return 0
  local kept=$DIR/secrets/keycloak.env
  # shellcheck disable=SC2016 # runs in the container, which expands its own variables
  printf '%s\n%s\n' "$(value "$kept" KC_OPENPLANNER_CLIENT_SECRET)" "$(value "$kept" KC_DIRECTORY_CLIENT_SECRET)" \
    | idp_admin '
      umask 077
      read -r admin && read -r directory || exit 1
      secret() { # secret CLIENT VALUE: the new secret of one client in realm openplanner
        id=$(/opt/keycloak/bin/kcadm.sh get clients -r openplanner -q clientId="$1" --fields id --format csv --noquotes) \
          && [ -n "$id" ] && printf "{\"secret\":\"%s\"}" "$2" >/tmp/op-secret.json \
          && /opt/keycloak/bin/kcadm.sh update "clients/$id" -r openplanner -f /tmp/op-secret.json >/dev/null 2>&1
        rc=$?; rm -f /tmp/op-secret.json; return $rc
      }
      secret openplanner-admin "$admin" && secret openplanner-directory "$directory"' \
    || die "the bundled identity provider did not take its new client secrets within ${HEALTH_TIMEOUT}s, so staff cannot log in" \
      "Check that KC_BOOTSTRAP_ADMIN_PASSWORD in secrets/keycloak.env is the password of 'admin' in realm master (the runbook's recovery after an unexplained outage changes it), then run this script again: it sets the same new secrets."
  rm -f "$ROTATION_MARK"
  log "the bundled identity provider took its new client secrets; the old ones no longer work (ADR 0125)"
}

# idp_admin SCRIPT: run SCRIPT with sh in the idp container, logged in with kcadm.sh to realm
# master as `admin`. The password is the container's own KC_BOOTSTRAP_ADMIN_PASSWORD and reaches
# kcadm.sh as KC_CLI_PASSWORD, never as an argument (which ps shows); this function's stdin
# becomes SCRIPT's, so values travel the same way. Retried until it succeeds or HEALTH_TIMEOUT
# passes, since Keycloak takes a while to start; returns 1 then.
idp_admin() {
  local input path deadline=$((SECONDS + HEALTH_TIMEOUT))
  input=$(cat)
  path=$(value "$DIR/secrets/keycloak.env" KC_HTTP_RELATIVE_PATH)
  until printf '%s\n' "$input" | compose exec -T idp sh -c "
      KC_CLI_PASSWORD=\$KC_BOOTSTRAP_ADMIN_PASSWORD /opt/keycloak/bin/kcadm.sh config credentials \
        --server http://localhost:8080${path} --realm master --user admin </dev/null >/dev/null 2>&1 || exit 1
      $1" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || return 1
    sleep 5
  done
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
UPDATE_PROXY_IMAGE=$(proxy_image_name)
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
  if [[ $CONFIG_KIND == sqlite ]]; then
    # The package is read by the pinned image, so the release comes first (ADR 0089 point 8).
    # A document carries no login, so it needs none of this: the login is asked for as usual.
    pin_release
    package_copy
    package_env
    settings
  else
    settings
    pin_release
    [[ $CONFIG_KIND != document ]] || document_copy
  fi
  start
  case $CONFIG_KIND in
    sqlite) import_package ;;
    document) apply_document ;;
  esac
  if ((UPDATER)); then
    install_updater
  else
    warn "--no-updater: updates requested in the portal will wait"
  fi
  log "installed in $DIR ($MODE): $(value "$ENV_FILE" PUBLIC_BASE_URL)"
  log "every compose command here: docker compose --env-file .env.update <command>"
}

main "$@"
