#!/usr/bin/env bash
# The verification stack of the verify-openplanner skill (../SKILL.md): this checkout built and
# started next to any other stack on the machine, driven by journey.mjs, removed again by `down`.
#
#   stack.sh up        build this checkout as openplanner:verify, start web, worker, Mailpit and
#                      Keycloak on ports of their own, load the demo groups, then run doctor
#   stack.sh doctor    read-only: is the running stack ours, built from this checkout, usable?
#   stack.sh compose   docker compose with this stack's project, files and ports, for example
#                      `stack.sh compose logs --tail 50 worker`
#   stack.sh down      keep the app logs (scanned for booking-link tokens and participant values,
#                      which are blanked), remove the containers, network and volumes of this
#                      stack, keep the evidence
#
# Optional environment: VERIFY_WEB_PORT (8010), VERIFY_KC_PORT (8090), VERIFY_MAIL_PORT (8035),
# VERIFY_PROJECT (openplanner-verify), VERIFY_EVIDENCE_ROOT (<checkout>/.verify-openplanner),
# VERIFY_READY_TIMEOUT (600 seconds). The evidence of a run stays in <evidence root>/<run id>/.
# Runs on the bash of macOS (3.2) as well; needs docker with compose v2.24 or newer, git, curl.
set -Eeuo pipefail

SKILL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
REPO=$(git -C "$SKILL_DIR" rev-parse --show-toplevel)
OVERRIDE="$SKILL_DIR/compose.verify.yml"
EVIDENCE_ROOT=${VERIFY_EVIDENCE_ROOT:-$REPO/.verify-openplanner}
CURRENT="$EVIDENCE_ROOT/current"
IMAGE=openplanner:verify
READY_TIMEOUT=${VERIFY_READY_TIMEOUT:-600}
DEMO_NAME="Demo: videogesprek"
# A booking-link token in a URL path (/b/<token>); /b/m/<reference> does not match.
TOKEN_RE='/b/[A-Za-z0-9_-]{20,}'

if command -v sha256sum >/dev/null 2>&1; then SHA=(sha256sum); else SHA=(shasum -a 256); fi

say() { printf '%s\n' "$*"; }
fail() {
  printf 'stack.sh: %s\n' "$*" >&2
  exit 1
}

# The value of KEY in the state file of the current run.
state() { sed -n "s/^$1=//p" "$RUN_DIR/stack.env" | head -n 1; }

# Set KEY to VALUE in the state file of the current run.
set_state() {
  grep -v "^$1=" "$RUN_DIR/stack.env" >"$RUN_DIR/stack.env.new" || true
  printf '%s=%s\n' "$1" "$2" >>"$RUN_DIR/stack.env.new"
  mv "$RUN_DIR/stack.env.new" "$RUN_DIR/stack.env"
}

# Load the current run that `up` recorded; false when there is none.
load_run() {
  [ -f "$CURRENT" ] || return 1
  RUN_ID=$(cat "$CURRENT")
  # The id becomes a path that `down` deletes from; accept only what `up` writes.
  case "$RUN_ID" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) fail "$CURRENT does not hold a run id (YYYYMMDD-HHMMSS); delete it, then run down" ;;
  esac
  RUN_DIR="$EVIDENCE_ROOT/$RUN_ID"
  [ -f "$RUN_DIR/stack.env" ] || fail "run $RUN_ID has no $RUN_DIR/stack.env; delete $CURRENT, then run down"
  PROJECT=$(state VERIFY_PROJECT)
  WEB_PORT=$(state VERIFY_WEB_PORT)
  KC_PORT=$(state VERIFY_KC_PORT)
  MAIL_PORT=$(state VERIFY_MAIL_PORT)
}

# docker compose for this stack only: own project name, the override, the run's ports.
compose() {
  docker compose -p "$PROJECT" -f "$REPO/docker-compose.yml" -f "$OVERRIDE" \
    --env-file "$RUN_DIR/compose.env" --profile dev "$@"
}

# Container ids of the project, running or not.
project_containers() {
  docker ps -aq --filter "label=com.docker.compose.project=$PROJECT"
}

# Remove the containers, networks and volumes that carry this project's compose label: the way
# out when no run is recorded, since compose without the run's files finds nothing to remove.
remove_by_label() {
  local label="label=com.docker.compose.project=$PROJECT"
  project_containers | xargs -r docker rm -f >/dev/null
  docker network ls -q --filter "$label" | xargs -r docker network rm >/dev/null
  docker volume ls -q --filter "$label" | xargs -r docker volume rm >/dev/null
}

check_docker() {
  command -v docker >/dev/null 2>&1 || fail "docker is not installed"
  docker info >/dev/null 2>&1 || fail "docker is not running; start Docker Desktop (or the docker service)"
  docker compose version >/dev/null 2>&1 || fail "docker compose v2 is missing"
  command -v curl >/dev/null 2>&1 || fail "curl is not installed"
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# A port goes into compose files and a sed expression: a number from 1024 to 65535 only.
valid_port() {
  case "$1" in '' | *[!0-9]*) return 1 ;; esac
  [ "$1" -ge 1024 ] && [ "$1" -le 65535 ]
}

check_ports() {
  local port busy=""
  for port in "$@"; do
    if port_busy "$port"; then busy="$busy $port"; fi
  done
  [ -z "$busy" ] || fail "port(s)$busy on 127.0.0.1 are in use by something else; choose others with VERIFY_WEB_PORT, VERIFY_KC_PORT and VERIFY_MAIL_PORT"
}

# A hash of what the image is built from: the paths .dockerignore lets into the build context
# (an allow-list), as committed plus local changes and untracked files. Docs or skill edits
# outside those paths leave it unchanged, so doctor only calls a build stale when it is.
fingerprint() {
  local line
  local -a paths
  paths=()
  while IFS= read -r line; do
    case "$line" in
      '!'*)
        line=${line#!}
        paths+=("${line%/}")
        ;;
    esac
  done <"$REPO/.dockerignore"
  [ "${#paths[@]}" -gt 0 ] || fail ".dockerignore names no '!' paths any more; update fingerprint in stack.sh"
  {
    git -C "$REPO" ls-tree -r HEAD -- "${paths[@]}"
    git -C "$REPO" diff HEAD --binary -- "${paths[@]}"
    git -C "$REPO" ls-files -z --others --exclude-standard -- "${paths[@]}" \
      | (cd "$REPO" && xargs -0 -r "${SHA[@]}")
  } | "${SHA[@]}" | cut -d' ' -f1
}

# GET a URL from the host; prints the body, fails on an error status.
get() { curl -fsS --max-time 5 "$1"; }

# Wait until URL answers with a body that contains TEXT, or fail after SECONDS.
wait_for() {
  local url=$1 text=$2 seconds=$3 waited=0
  until get "$url" 2>/dev/null | grep -qF "$text"; do
    waited=$((waited + 2))
    [ "$waited" -le "$seconds" ] || return 1
    sleep 2
  done
}

# Copy realm files of dev/keycloak/ for this stack: the redirect, origin and logout addresses
# of the admin client name this stack's web port instead of the development stack's 8000.
render_realms() {
  local name
  for name in openplanner-realm.json andere-realm.json; do
    grep -qF 'http://127.0.0.1:8000' "$REPO/dev/keycloak/$name" \
      || fail "dev/keycloak/$name no longer names http://127.0.0.1:8000; update render_realms in stack.sh"
    sed "s#http://127.0.0.1:8000#http://127.0.0.1:$WEB_PORT#g" "$REPO/dev/keycloak/$name" >"$RUN_DIR/scratch/$name"
  done
}

# EXIT trap of `up`: after a failed start, say where its logs are and how to clean up.
on_up_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] \
    || printf 'stack.sh: up failed; its logs are in %s. Remove what it started with: %s down\n' \
      "$RUN_DIR" "$0" >&2
}

cmd_up() {
  check_docker
  if load_run; then
    fail "a verification stack is already up (run $RUN_ID, project $PROJECT); run doctor, or down first"
  fi
  PROJECT=${VERIFY_PROJECT:-openplanner-verify}
  WEB_PORT=${VERIFY_WEB_PORT:-8010}
  KC_PORT=${VERIFY_KC_PORT:-8090}
  MAIL_PORT=${VERIFY_MAIL_PORT:-8035}
  local port
  for port in "$WEB_PORT" "$KC_PORT" "$MAIL_PORT"; do
    valid_port "$port" || fail "port '$port' is not a number from 1024 to 65535"
  done
  [ "$WEB_PORT" != "$KC_PORT" ] && [ "$WEB_PORT" != "$MAIL_PORT" ] && [ "$KC_PORT" != "$MAIL_PORT" ] \
    || fail "VERIFY_WEB_PORT, VERIFY_KC_PORT and VERIFY_MAIL_PORT must differ"
  case "$PROJECT" in
    [a-z0-9]*) case "$PROJECT" in *[!a-z0-9_-]*) fail "VERIFY_PROJECT may hold only a-z, 0-9, _ and -" ;; esac ;;
    *) fail "VERIFY_PROJECT must start with a-z or 0-9" ;;
  esac
  [ -z "$(project_containers)" ] \
    || fail "project $PROJECT has containers that no current run owns; $0 down removes them"
  check_ports "$WEB_PORT" "$KC_PORT" "$MAIL_PORT"

  RUN_ID=$(date -u +%Y%m%d-%H%M%S)
  RUN_DIR="$EVIDENCE_ROOT/$RUN_ID"
  mkdir -p "$RUN_DIR/scratch"
  # Recorded first, so `down` can remove whatever a failed start leaves behind.
  cat >"$RUN_DIR/stack.env" <<ENV
VERIFY_RUN_ID=$RUN_ID
VERIFY_PROJECT=$PROJECT
VERIFY_WEB_PORT=$WEB_PORT
VERIFY_KC_PORT=$KC_PORT
VERIFY_MAIL_PORT=$MAIL_PORT
VERIFY_STATE=starting
PARTICIPANT_FIRST_NAME=Proefdeelnemer
PARTICIPANT_LAST_NAME=Run-$RUN_ID
PARTICIPANT_EMAIL=verify.$RUN_ID@example.org
ENV
  cat >"$RUN_DIR/compose.env" <<ENV
VERIFY_WEB_PORT=$WEB_PORT
VERIFY_KC_PORT=$KC_PORT
VERIFY_MAIL_PORT=$MAIL_PORT
VERIFY_SCRATCH=$RUN_DIR/scratch
ENV
  printf '%s\n' "$RUN_ID" >"$CURRENT"
  trap on_up_exit EXIT
  render_realms

  set_state BUILT_FROM_COMMIT "$(git -C "$REPO" rev-parse HEAD)"
  set_state BUILT_FROM_FINGERPRINT "$(fingerprint)"
  if [ -z "$(git -C "$REPO" status --porcelain)" ]; then
    set_state BUILT_WITH_LOCAL_CHANGES no
  else
    set_state BUILT_WITH_LOCAL_CHANGES yes
  fi
  say "Building $IMAGE from $REPO (log: $RUN_DIR/build.log; a first build takes a few minutes)..."
  if ! docker build -t "$IMAGE" "$REPO" >"$RUN_DIR/build.log" 2>&1; then
    tail -n 30 "$RUN_DIR/build.log" >&2
    fail "the image did not build (log above)"
  fi
  set_state IMAGE_ID "$(docker image inspect -f '{{.Id}}' "$IMAGE")"

  say "Starting web, worker, Mailpit and Keycloak as project $PROJECT..."
  if ! compose up -d --no-build --wait --wait-timeout "$READY_TIMEOUT" >"$RUN_DIR/compose-up.log" 2>&1; then
    tail -n 20 "$RUN_DIR/compose-up.log" >&2
    compose ps --all >&2 || true
    fail "the stack did not become healthy within $READY_TIMEOUT seconds"
  fi
  wait_for "http://127.0.0.1:$KC_PORT/realms/openplanner/.well-known/openid-configuration" '"issuer"' 180 \
    || fail "Keycloak did not serve the openplanner realm within 180 seconds"
  wait_for "http://127.0.0.1:$WEB_PORT/readyz" '"status":"ok"' 120 \
    || fail "web did not answer /readyz with ok within 120 seconds"

  compose exec -T web python -m app.cli seed-demo >"$RUN_DIR/seed.txt"
  set_state INSTANCE_ID "$(get "http://127.0.0.1:$WEB_PORT/api/v1/instance" | sed -n 's/.*"instance_id":"\([^"]*\)".*/\1/p')"
  set_state STARTED_AT "$(date -u +%FT%TZ)"
  set_state VERIFY_STATE up
  trap - EXIT

  cmd_doctor
  cat <<TXT

Verification stack up (run $RUN_ID, project $PROJECT):
  Participant pages   http://127.0.0.1:$WEB_PORT/g/demo
  Admin portal        http://127.0.0.1:$WEB_PORT/admin/   (Keycloak http://127.0.0.1:$KC_PORT)
  Mail                http://127.0.0.1:$MAIL_PORT          (Mailpit catches every message)
  Evidence            $RUN_DIR
Drive it: node $SKILL_DIR/scripts/journey.mjs all     Remove it: $0 down
TXT
}

# The read-only checks; prints one line per check, fails when any check failed.
doctor_checks() {
  local failures=0 line svc ps_out want got body image_now fp cport hport
  pass() { printf 'PASS  %-10s %s\n' "$1" "$2"; }
  warn() { printf 'WARN  %-10s %s\n' "$1" "$2"; }
  bad() {
    printf 'FAIL  %-10s %s\n' "$1" "$2"
    failures=$((failures + 1))
  }

  say "doctor $(date -u +%FT%TZ)  run $RUN_ID  project $PROJECT  state $(state VERIFY_STATE)"

  ps_out=$(compose ps --all --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null || true)
  line=""
  for svc in web worker keycloak mailpit; do
    got=$(printf '%s\n' "$ps_out" | sed -n "s/^$svc //p" | head -n 1)
    got=${got% } # no health check: an empty last column
    case "$svc:$got" in
      web:"running healthy" | worker:"running healthy" | keycloak:running* | mailpit:running*) line="$line, $svc $got" ;;
      *) line="$line, $svc ${got:-missing} (NOT OK)" ;;
    esac
  done
  case "$line" in
    *"NOT OK"*) bad containers "${line#, }" ;;
    *) pass containers "${line#, }" ;;
  esac

  line=""
  for want in "web 8000 $WEB_PORT" "keycloak 8080 $KC_PORT" "mailpit 8025 $MAIL_PORT"; do
    read -r svc cport hport <<<"$want"
    got=$(compose port "$svc" "$cport" 2>/dev/null || true)
    if [ "$got" = "127.0.0.1:$hport" ]; then
      line="$line, $svc $got"
    else
      line="$line, $svc ${got:-unpublished} (expected 127.0.0.1:$hport, NOT OK)"
    fi
  done
  case "$line" in
    *"NOT OK"*) bad ports "${line#, }" ;;
    *) pass ports "${line#, } (published by this project)" ;;
  esac

  got=$(get "http://127.0.0.1:$WEB_PORT/api/v1/instance" 2>/dev/null | sed -n 's/.*"instance_id":"\([^"]*\)".*/\1/p' || true)
  if [ -n "$got" ] && [ "$got" = "$(state INSTANCE_ID)" ]; then
    pass instance "http://127.0.0.1:$WEB_PORT answers the installation recorded at up ($got)"
  else
    bad instance "http://127.0.0.1:$WEB_PORT answers installation '${got:-none}', expected '$(state INSTANCE_ID)': not this run's stack"
  fi

  body=$(get "http://127.0.0.1:$WEB_PORT/readyz" 2>/dev/null || true)
  case "$body" in
    *'"status":"ok"'*) pass ready "/readyz ok" ;;
    *'"status":"degraded"'*) warn ready "/readyz degraded: the data volume is low on space" ;;
    *) bad ready "/readyz answered '${body:-nothing}'" ;;
  esac

  image_now=$(docker inspect -f '{{.Image}}' "$(compose ps -q web 2>/dev/null | head -n 1)" 2>/dev/null || true)
  fp=$(fingerprint)
  if [ "$image_now" != "$(state IMAGE_ID)" ]; then
    bad build "web runs image ${image_now:-none}, not the $IMAGE built at up ($(state IMAGE_ID))"
  elif [ "$fp" != "$(state BUILT_FROM_FINGERPRINT)" ]; then
    bad build "files the image is built from (.dockerignore) changed since up; run down, then up"
  else
    pass build "$IMAGE $(state IMAGE_ID | cut -c1-19), built from $(state BUILT_FROM_COMMIT | cut -c1-12) (local changes: $(state BUILT_WITH_LOCAL_CHANGES)); its files are unchanged since"
  fi

  if get "http://127.0.0.1:$WEB_PORT/g/demo" 2>/dev/null | grep -qF "$DEMO_NAME"; then
    pass demo "/g/demo shows \"$DEMO_NAME\""
  else
    bad demo "/g/demo does not show \"$DEMO_NAME\"; is the demo seeded?"
  fi

  want="http://127.0.0.1:$KC_PORT/realms/openplanner"
  got=$(get "$want/.well-known/openid-configuration" 2>/dev/null | sed -n 's/.*"issuer":"\([^"]*\)".*/\1/p' || true)
  if [ "$got" != "$want" ]; then
    bad login "Keycloak announces issuer '${got:-none}', expected $want"
  elif compose exec -T web python -c 'import json, os, sys, urllib.request
doc = json.load(urllib.request.urlopen(os.environ["OIDC_DISCOVERY_URL"], timeout=5))
sys.exit(0 if doc["issuer"] == os.environ["OIDC_ISSUER"] else 1)' >/dev/null 2>&1; then
    pass login "issuer $got, discovered by web as configured"
  else
    bad login "web cannot read its OIDC discovery document, or the issuer differs from OIDC_ISSUER"
  fi

  body=$(get "http://127.0.0.1:$MAIL_PORT/api/v1/info" 2>/dev/null || true)
  if [ -z "$body" ]; then
    bad mail "Mailpit does not answer at http://127.0.0.1:$MAIL_PORT"
  elif compose exec -T web python -c 'import socket; socket.create_connection(("mailpit", 1025), timeout=5).close()' >/dev/null 2>&1; then
    pass mail "Mailpit at http://127.0.0.1:$MAIL_PORT; web reaches mailpit:1025"
  else
    bad mail "web cannot reach mailpit:1025 (SMTP)"
  fi

  if [ "$failures" -eq 0 ]; then
    say "doctor: usable"
  else
    say "doctor: NOT usable ($failures failed); see the lines above, or start over with down, then up"
  fi
  [ "$failures" -eq 0 ]
}

cmd_doctor() {
  check_docker
  load_run || fail "no verification stack of this skill is up (no $CURRENT); run: $0 up"
  local report rc=0
  report=$(doctor_checks) || rc=$?
  printf '%s\n' "$report" | tee -a "$RUN_DIR/doctor.txt"
  return "$rc"
}

# Keep the app's logs with booking-link tokens and this run's participant values blanked, and
# count how many lines held them: any count above zero is a finding against the privacy rules.
save_logs() {
  local raw="$RUN_DIR/scratch/app-raw.log" email last first tokens values errors
  local error_re='"log\.level": ?"(error|critical)"'
  email=$(state PARTICIPANT_EMAIL)
  last=$(state PARTICIPANT_LAST_NAME)
  first=$(state PARTICIPANT_FIRST_NAME)
  mkdir -p "$RUN_DIR/logs"
  compose logs --no-color --timestamps web worker >"$raw" 2>&1 || true
  tokens=$(grep -cE "$TOKEN_RE" "$raw" || true)
  values=$(grep -cF -e "$email" -e "$last" -e "$first" "$raw" || true)
  errors=$(grep -cE "$error_re" "$raw" || true)
  sed -E -e "s#$TOKEN_RE#/b/<token>#g" -e "s#$email#<email>#g" -e "s#$last#<last_name>#g" \
    -e "s#$first#<first_name>#g" "$raw" >"$RUN_DIR/logs/app.log"
  {
    say "Scan of the web and worker logs of run $RUN_ID ($(wc -l <"$raw" | tr -d ' ') lines)"
    say "lines with a booking-link token (/b/<token>): $tokens"
    say "lines with a participant value of this run: $values"
    say "lines at level error or critical: $errors, by event.action:"
    grep -E "$error_re" "$raw" | sed -n 's/.*"event\.action": \{0,1\}"\([^"]*\)".*/\1/p' \
      | sort | uniq -c | sed 's/^ */  /' || true
  } >"$RUN_DIR/logs/scan.txt"
  cat "$RUN_DIR/logs/scan.txt"
  rm -f "$raw"
}

cmd_down() {
  check_docker
  if ! load_run; then
    PROJECT=${VERIFY_PROJECT:-openplanner-verify}
    if [ -n "$(project_containers)" ]; then
      say "No current run is recorded, but project $PROJECT has containers; removing them by its label."
      remove_by_label
      [ -z "$(project_containers)" ] || fail "containers of project $PROJECT remain; see docker ps -a"
      say "Removed the containers, networks and volumes of project $PROJECT."
    else
      say "Nothing to remove: no verification stack is up."
    fi
    return 0
  fi
  save_logs
  compose down --volumes --remove-orphans >"$RUN_DIR/logs/compose-down.log" 2>&1 \
    || fail "compose down failed; see $RUN_DIR/logs/compose-down.log"
  [ -z "$(project_containers)" ] || fail "containers of project $PROJECT remain; see docker ps -a"
  rm -rf "$RUN_DIR/scratch"
  set_state VERIFY_STATE down
  set_state STOPPED_AT "$(date -u +%FT%TZ)"
  rm -f "$CURRENT"
  [ -d "$RUN_DIR" ] || fail "the evidence of run $RUN_ID is gone: $RUN_DIR"
  say "Removed project $PROJECT (containers, network, volumes); the image $IMAGE stays for the next build."
  say "Evidence kept: $RUN_DIR ($(find "$RUN_DIR" -type f | wc -l | tr -d ' ') files)"
}

cmd_compose() {
  load_run || fail "no verification stack of this skill is up (no $CURRENT); run: $0 up"
  compose "$@"
}

cmd=${1:-help}
[ "$#" -eq 0 ] || shift
case "$cmd" in
  up) cmd_up ;;
  doctor) cmd_doctor ;;
  down) cmd_down ;;
  compose) cmd_compose "$@" ;;
  -h | --help | help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}" ;;
  *) fail "unknown command '$cmd' (up, doctor, compose, down)" ;;
esac
