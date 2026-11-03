#!/usr/bin/env bash
# Try OpenPlanner on any computer with Docker (docs/try-it.md): macOS, Linux, Windows with WSL
# or Git Bash. For a server, use scripts/install.sh instead.
#
#   ./scripts/try.sh [up|status|logs|down|reset] [--no-open]
#
#   up      (default) check Docker, write .env from .env.example, build and start web, worker,
#           Mailpit and the development Keycloak, wait until ready, load the demo group
#   status  show the containers and the addresses
#   logs    follow the logs of every container (Ctrl-C leaves them running)
#   down    stop everything, keep the data
#   reset   stop everything and delete the data (database, test users' sessions)
#
# Safe to run again: nothing is overwritten that is in order. Every address is on 127.0.0.1;
# nothing is reachable from the network. The accounts are development-only (dev/keycloak/README.md).
set -Eeuo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$DIR"
# not a pin: the oldest Compose that reads env_file with path/required
COMPOSE_MIN=2.24.0
READY_TIMEOUT=${READY_TIMEOUT:-600}
PORTS=(8000 8080 8025)
WEB=http://127.0.0.1:8000

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# True when $1 is the same as or newer than $2 (dotted numbers).
version_at_least() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

check_docker() {
  command -v docker >/dev/null 2>&1 \
    || fail "Docker is not installed. Install Docker Desktop (macOS, Windows) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 \
    || fail "Docker is installed but not running (or your account may not use it). Start Docker Desktop, or on Linux: sudo systemctl start docker and add yourself to the docker group."
  local compose
  compose=$(docker compose version --short 2>/dev/null || true)
  [ -n "$compose" ] || fail "Docker Compose v2 is missing. Update Docker Desktop, or install the docker-compose-plugin package."
  version_at_least "${compose#v}" "$COMPOSE_MIN" \
    || fail "Docker Compose ${compose} is too old; ${COMPOSE_MIN} or newer is needed. Update Docker."
  # Podman answers to the docker command too, but its compose does not read this project's files
  # (env_file path/required, fixed networks); say so before it fails halfway.
  if docker version 2>/dev/null | grep -qi podman; then
    say "Warning: this looks like Podman, which OpenPlanner does not support (docs/try-it.md). Install Docker Desktop, Colima or Rancher Desktop (dockerd mode) if the start fails."
  fi
}

# Ports held by something else would make the start fail halfway; say so first. Our own
# containers holding them is fine (a second run).
check_ports() {
  local running busy=() port
  running=$(docker compose --profile dev ps --quiet 2>/dev/null || true)
  [ -z "$running" ] || return 0
  for port in "${PORTS[@]}"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then busy+=("$port"); fi
  done
  [ "${#busy[@]}" -eq 0 ] || fail "port(s) ${busy[*]} on this computer are in use by another program. Stop it and run this again."
}

ensure_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    say "Wrote .env from .env.example (development settings)."
  fi
}

# The app answers /readyz when the database is migrated; asked from inside the container so
# neither curl nor wget is needed on the computer.
wait_ready() {
  local waited=0
  say "Building and starting (the first run downloads and builds for a few minutes)..."
  while ! docker compose exec -T web python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).status == 200 else 1)" \
    >/dev/null 2>&1; do
    waited=$((waited + 3))
    [ "$waited" -le "$READY_TIMEOUT" ] || {
      docker compose --profile dev logs --tail 40 web >&2 || true
      fail "the application did not become ready within ${READY_TIMEOUT} seconds (log above)."
    }
    sleep 3
  done
}

open_browser() {
  [ "${1:-}" = "--no-open" ] && return 0
  if command -v open >/dev/null 2>&1; then open "$WEB/g/demo" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$WEB/g/demo" >/dev/null 2>&1 || true
  elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$WEB/g/demo" >/dev/null 2>&1 || true
  fi
}

addresses() {
  cat <<TXT

OpenPlanner is running.

  Book as a participant   $WEB/g/demo
  Admin portal            $WEB/admin/
  Mail that was "sent"    http://127.0.0.1:8025   (Mailpit: confirmation and reminder emails)

Log in to the portal with a development account; every password is: dev-only-password
  super       everything (start here)         orgadmin    one organisation
  groupadmin  one group                       auditor     read-only audit
  agent       the agent's queue               norole      logged in without rights

Stop:   ./scripts/try.sh down      (keeps your data)
Start over with empty data:   ./scripts/try.sh reset
TXT
}

cmd_up() {
  check_docker
  check_ports
  ensure_env
  docker compose --profile dev up --build -d
  wait_ready
  docker compose exec -T web python -m app.cli seed-demo
  addresses
  open_browser "${1:-}"
}

cmd=${1:-up}
case "$cmd" in
  up) cmd_up "${2:-}" ;;
  --no-open) cmd_up --no-open ;;
  status) check_docker; docker compose --profile dev ps; addresses ;;
  logs) check_docker; docker compose --profile dev logs -f --tail 100 ;;
  down) check_docker; docker compose --profile dev down; say "Stopped. Your data is kept; ./scripts/try.sh starts it again." ;;
  reset)
    check_docker
    docker compose --profile dev down --volumes
    say "Stopped and deleted the data. ./scripts/try.sh starts a clean installation."
    ;;
  -h|--help|help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown command '$cmd' (up, status, logs, down, reset)" ;;
esac
