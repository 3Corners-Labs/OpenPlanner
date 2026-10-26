#!/usr/bin/env bash
# OpenPlanner host updater (ADR 0028). Run every minute by openplanner-updater.timer.
#
# Carries out the update requests a superuser makes in the admin portal. The application never
# touches Docker: it only stores requests, and this script talks to it through
# `python -m app.cli` inside the web container.
#
#   update   pull <image>:<version> -> digest -> cosign verify -> record previous digest and
#            Alembic revision -> backup -> pin digest -> up -> healthy?
#              yes: report succeeded
#              no:  restore backup, pin previous digest, up -> report rolled_back
#   restore  (within 24 h of an update) backup -> alembic downgrade <previous revision>
#            -> pin previous digest -> up -> healthy?
#              yes: report succeeded
#              no:  restore backup, pin the newer digest again -> report rolled_back
#
# Configuration: /etc/openplanner/updater.env (see updater.env.example). The pinned image is
# OPENPLANNER_IMAGE in $COMPOSE_DIR/.env.update; production compose commands pass
# `--env-file .env.update`. Backups live in the data volume and expire after 24 hours.
set -Eeuo pipefail

CONFIG=${OPENPLANNER_UPDATER_CONFIG:-/etc/openplanner/updater.env}
# shellcheck source=/dev/null
. "$CONFIG"
: "${COMPOSE_DIR:?COMPOSE_DIR is not set}"
: "${UPDATE_IMAGE:?UPDATE_IMAGE is not set}"
: "${CERT_IDENTITY_REGEXP:?CERT_IDENTITY_REGEXP is not set}"
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
READY_URL=${READY_URL:-http://127.0.0.1:8000/readyz}
STATE_DIR=${STATE_DIR:-/var/lib/openplanner-updater}
PIN_FILE="$COMPOSE_DIR/.env.update"
STATE_FILE="$STATE_DIR/previous.env"
OIDC_ISSUER=https://token.actions.githubusercontent.com

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
compose() { docker compose --project-directory "$COMPOSE_DIR" --env-file "$PIN_FILE" "$@"; }
app() { compose exec -T web python -m app.cli "$@"; }
# One-off command in a fresh web container with the pinned image (web and worker stopped).
offline() { compose run --rm --no-deps -T web "$@"; }
pinned() { sed -n 's/^OPENPLANNER_IMAGE=//p' "$PIN_FILE"; }
# Everything that comes out of the container is data, never shell: it is checked against these
# patterns before use, and the state file is read with sed, never sourced.
VERSION_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
REVISION_RE='^[0-9A-Za-z_]{1,32}$'
IMAGE_RE='^[a-z0-9./_-]+(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$'
state() { sed -n "s/^$1=//p" "$STATE_FILE"; }
pin() { printf 'OPENPLANNER_IMAGE=%s\n' "$1" >"$PIN_FILE.tmp" && mv "$PIN_FILE.tmp" "$PIN_FILE"; }

report() { # report ID STATUS [REASON]; never fails the script (the old version may lack it)
  if app update-report "$@" >/dev/null; then
    log "request $1: $2 ${3:-}"
  else
    log "request $1: $2 ${3:-} (could not be recorded in the portal)"
  fi
}

healthy() { # web answers /readyz and the worker container reports healthy
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

switch_to() { # pin an image and (re)start web and worker
  pin "$1"
  compose up -d web worker
}

put_back() { # put_back BACKUP IMAGE: restore the backup, run IMAGE again
  local backup=$1 image=$2
  compose stop web worker
  # The restore runs with the pinned (newer) image, which knows `restore-db`; if that image is
  # too broken to run at all, the one we return to tries.
  if ! offline python -m app.cli restore-db "$backup"; then
    pin "$image"
    offline python -m app.cli restore-db "$backup" || return 1
  fi
  switch_to "$image"
  healthy
}

resolve() { # resolve VERSION: pull the release image and print its digest reference
  docker pull -q "$UPDATE_IMAGE:$1" >/dev/null 2>&1 || return 1
  docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$UPDATE_IMAGE:$1" \
    | grep -m1 "^$UPDATE_IMAGE@sha256:"
}

do_update() { # do_update ID TARGET FROM
  local id=$1 target=$2 from=$3 previous digest revision backup="request-$1"
  previous=$(pinned)
  if ! digest=$(resolve "$target"); then
    report "$id" failed image_not_found
    return
  fi
  if ! cosign verify "$digest" \
    --certificate-identity-regexp "$CERT_IDENTITY_REGEXP" \
    --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1; then
    report "$id" failed signature_invalid
    return
  fi
  revision=$(compose exec -T web alembic current 2>/dev/null | awk 'NR==1 {print $1}')
  if ! [[ $revision =~ $REVISION_RE && $previous =~ $IMAGE_RE ]] \
    || ! app backup-db "$backup" >/dev/null; then
    report "$id" failed backup_failed
    return
  fi
  printf 'PREVIOUS_IMAGE=%s\nPREVIOUS_REVISION=%s\nPREVIOUS_VERSION=%s\nNEWER_IMAGE=%s\n' \
    "$previous" "$revision" "$from" "$digest" >"$STATE_FILE"
  log "request $id: $from -> $target ($digest)"
  switch_to "$digest"
  if healthy; then
    report "$id" succeeded
    return
  fi
  log "request $id: $target did not become healthy; rolling back"
  if put_back "$backup" "$previous"; then
    report "$id" rolled_back health_check_failed
  else
    report "$id" failed restore_failed
  fi
  rm -f "$STATE_FILE"
}

do_restore() { # do_restore ID TARGET
  local id=$1 target=$2 backup="request-$1-restore"
  local PREVIOUS_IMAGE PREVIOUS_REVISION PREVIOUS_VERSION NEWER_IMAGE
  if [[ ! -f $STATE_FILE ]]; then
    report "$id" failed updater_error
    return
  fi
  PREVIOUS_IMAGE=$(state PREVIOUS_IMAGE)
  PREVIOUS_REVISION=$(state PREVIOUS_REVISION)
  PREVIOUS_VERSION=$(state PREVIOUS_VERSION)
  NEWER_IMAGE=$(state NEWER_IMAGE)
  if [[ $PREVIOUS_VERSION != "$target" ]] \
    || ! [[ $PREVIOUS_IMAGE =~ $IMAGE_RE && $NEWER_IMAGE =~ $IMAGE_RE ]] \
    || ! [[ $PREVIOUS_REVISION =~ $REVISION_RE ]]; then
    report "$id" failed updater_error
    return
  fi
  if ! app backup-db "$backup" >/dev/null; then
    report "$id" failed backup_failed
    return
  fi
  compose stop web worker
  # The newer image knows its own migrations, so it takes the schema down.
  if ! offline alembic downgrade "$PREVIOUS_REVISION"; then
    switch_to "$NEWER_IMAGE"
    report "$id" failed migration_failed
    return
  fi
  log "request $id: back to $target ($PREVIOUS_IMAGE)"
  switch_to "$PREVIOUS_IMAGE"
  if healthy; then
    report "$id" succeeded
    rm -f "$STATE_FILE"
    return
  fi
  log "request $id: $target did not become healthy; returning to the newer version"
  pin "$NEWER_IMAGE"
  if put_back "$backup" "$NEWER_IMAGE"; then
    report "$id" rolled_back health_check_failed
  else
    report "$id" failed restore_failed
  fi
}

main() {
  mkdir -p "$STATE_DIR"
  exec 9>"$STATE_DIR/lock"
  flock -n 9 || exit 0 # an update is still running
  if [[ ! -f $PIN_FILE ]]; then
    log "missing $PIN_FILE: pin the running image first (docs/runbook.md, Updates)"
    exit 1
  fi
  app backup-db --prune >/dev/null || log "could not prune old backups"
  local claim id action target from
  claim=$(app update-claim) || { log "could not ask for requests"; exit 1; }
  [[ -n $claim ]] || exit 0
  read -r id action target from <<<"$claim"
  if ! [[ $id =~ ^[0-9]{1,9}$ ]]; then
    log "unexpected claim from the container; ignored"
    exit 1
  fi
  if ! [[ $target =~ $VERSION_RE && $from =~ $VERSION_RE ]]; then
    report "$id" failed updater_error
    exit 1
  fi
  trap 'report "$id" failed updater_error' ERR
  case $action in
    update) do_update "$id" "$target" "$from" ;;
    restore) do_restore "$id" "$target" ;;
    *) report "$id" failed updater_error ;;
  esac
}

main "$@"
