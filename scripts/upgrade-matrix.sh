#!/usr/bin/env bash
# The upgrade matrix of ADR 0116 point 7: take databases that released versions wrote through
# this checkout's migrations and back.
#
#   scripts/upgrade-matrix.sh window IMAGE_REPO   every fixture in the support window (the
#       newest patch of the newest two minors, and the stop), downloaded from the releases
#   scripts/upgrade-matrix.sh one FIXTURE [IMAGE] one fixture file; with IMAGE also the
#       downgrade and the start of the released image on it
#   scripts/upgrade-matrix.sh budget IMAGE        the schema step on a large fixture (100,000
#       bookings) that IMAGE writes, against UPGRADE_BUDGET_SECONDS
#   scripts/upgrade-matrix.sh fixture IMAGE VERSION  write upgrade-fixture-VERSION.sqlite and its
#       .sha256 into the current directory with the released IMAGE (main.yml, every release)
#
# Per fixture: read its state, `alembic upgrade head`, compare (rows per table, personal data
# on live and not on purged bookings, pending jobs claimable, settings within bounds), start
# this checkout on it (/readyz, /g/demo), `alembic downgrade` to the fixture's revision, start
# the released image on it, upgrade again and compare again. Needs gh (window), docker (IMAGE)
# and curl; PYTHON overrides the interpreter (default `uv run python`).
set -euo pipefail

# ADR 0116 "fixed on purpose": the migration budget mirrors HEALTH_TIMEOUT of the updater.
UPGRADE_BUDGET_SECONDS="${UPGRADE_BUDGET_SECONDS:-300}"
LARGE_BOOKINGS="${LARGE_BOOKINGS:-100000}"
PORT="${UPGRADE_PORT:-8011}"
read -r -a PY <<<"${PYTHON:-uv run python}"
# Development mode for this checkout's alembic, cli and app: a throwaway database, generated keys.
export ENVIRONMENT=dev
# The released image runs as this account, so it writes the work directory without opening it up.
AS_ME=(--user "$(id -u):$(id -g)")
work="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-matrix.XXXXXX")"
trap 'docker rm -f upgrade-app >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

alembic() { "${PY[@]}" -m alembic "$@"; }
cli() { "${PY[@]}" -m app.cli "$@"; }

wait_ready() {
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PORT/readyz" >/dev/null 2>&1; then
      curl -fsS -o /dev/null "http://127.0.0.1:$PORT/g/demo" && return 0
      echo "::error::$1: /g/demo did not answer" >&2
      return 1
    fi
    sleep 1
  done
  echo "::error::$1 did not become ready" >&2
  return 1
}

start_checkout() {
  "${PY[@]}" -m uvicorn app.main:create_app --factory --port "$PORT" \
    >"$work/uvicorn.log" 2>&1 &
  local pid=$!
  if ! wait_ready "this checkout"; then
    cat "$work/uvicorn.log" >&2
    kill "$pid" 2>/dev/null || true
    return 1
  fi
  kill "$pid"
  wait "$pid" 2>/dev/null || true
}

start_released() {
  local image="$1" name="$2"
  docker run -d --name upgrade-app "${AS_ME[@]}" --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges -e ENVIRONMENT=dev \
    -e "DATABASE_URL=sqlite+aiosqlite:////srv/data/$name" -v "$work:/srv/data" \
    -p "127.0.0.1:$PORT:8000" "$image" >/dev/null
  if ! wait_ready "$image"; then
    docker logs upgrade-app >&2
    return 1
  fi
  docker rm -f upgrade-app >/dev/null
}

one() {
  local fixture="$1" image="${2:-}" name
  name="$(basename "$fixture")"
  cp "$fixture" "$work/$name"
  export DATABASE_URL="sqlite+aiosqlite:///$work/$name"
  echo "== $name"
  cli upgrade-check --save "$work/before.json"
  local revision
  revision="$("${PY[@]}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$work/before.json")"
  alembic upgrade head
  cli upgrade-check --compare "$work/before.json"
  start_checkout
  if [ -n "$image" ]; then
    alembic downgrade "$revision"
    start_released "$image" "$name"
    alembic upgrade head
    cli upgrade-check --compare "$work/before.json"
  fi
  unset DATABASE_URL
}

window() {
  local repo="$1" tags tag version
  gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100" | jq -s 'add' >"$work/releases.json"
  # The stop (MIN_UPGRADE_FROM, --stop) joins the window once ADR 0116 step 5 bakes it in.
  tags="$("${PY[@]}" -m scripts.upgrade_window <"$work/releases.json")"
  if [ -z "$tags" ]; then
    echo "::notice::no release carries an upgrade fixture yet (ADR 0116 step 3); nothing to run"
    return 0
  fi
  for tag in $tags; do
    version="${tag#v}"
    local fixture="upgrade-fixture-$version.sqlite"
    gh release download "$tag" --dir "$work/download" --pattern "$fixture" --pattern "$fixture.sha256" --clobber
    (cd "$work/download" && sha256sum -c "$fixture.sha256")
    one "$work/download/$fixture" "$repo:$version"
  done
  # The schema step on the large fixture, written by the newest release in the window.
  budget "$repo:$(head -n 1 <<<"$tags" | sed 's/^v//')"
}

# Write a fixture with IMAGE into $work/NAME: a fresh database, migrated and seeded by IMAGE.
seed_with() {
  local image="$1" name="$2" bookings="$3"
  : >"$work/$name"
  local run=(docker run --rm "${AS_ME[@]}" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges
    -v "$work:/srv/data" -e ENVIRONMENT=dev -e "DATABASE_URL=sqlite+aiosqlite:////srv/data/$name")
  # `|| return 1`: `budget` calls this inside `if`, where `set -e` does not apply.
  "${run[@]}" "$image" alembic upgrade head || return 1
  "${run[@]}" "$image" python -m app.cli seed --upgrade-fixture --bookings "$bookings" || return 1
  # One file: fold the write-ahead log into the database before it leaves the container's view.
  "${run[@]}" "$image" python -c "import sqlite3,sys; sqlite3.connect(sys.argv[1]).execute('PRAGMA wal_checkpoint(TRUNCATE)')" "/srv/data/$name" || return 1
  rm -f "$work/$name-wal" "$work/$name-shm"
}

fixture() {
  local image="$1" version="$2" name
  name="upgrade-fixture-$version.sqlite"
  seed_with "$image" "$name" 0
  cp "$work/$name" "./$name"
  sha256sum "$name" >"$name.sha256"
  cat "$name.sha256"
}

budget() {
  local image="$1" name=large.sqlite seconds
  if ! seed_with "$image" "$name" "$LARGE_BOOKINGS"; then
    echo "::notice::$image cannot write the upgrade fixture yet (before ADR 0116 step 3); no budget run"
    return 0
  fi
  export DATABASE_URL="sqlite+aiosqlite:///$work/$name"
  local started=$SECONDS
  alembic upgrade head
  seconds=$((SECONDS - started))
  unset DATABASE_URL
  echo "schema step on $LARGE_BOOKINGS bookings: ${seconds}s (budget ${UPGRADE_BUDGET_SECONDS}s)"
  if [ "$seconds" -gt "$UPGRADE_BUDGET_SECONDS" ]; then
    echo "::error::the schema step took ${seconds}s on the large fixture; split it into a data migration job (ADR 0116 point 8)"
    return 1
  fi
}

case "${1:-}" in
  window) window "$2" ;;
  one) one "$2" "${3:-}" ;;
  budget) budget "$2" ;;
  fixture) fixture "$2" "$3" ;;
  *) sed -n '2,19p' "$0" >&2; exit 2 ;;
esac
