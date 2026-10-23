#!/usr/bin/env sh
# Start IMAGE hardened (read-only rootfs, non-root, no capabilities) on port 8000 as
# container "app", then wait until /readyz answers. Used by every CI job that needs a
# running app. Usage: scripts/run-app-container.sh <image> [extra docker run options...]
set -eu

image="$1"
shift
# Throwaway CI container: development mode (generated token key, http base URL).
docker run -d --name app --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -e ENVIRONMENT=dev \
  "$@" \
  -p 8000:8000 "$image" >/dev/null

if [ "$(docker exec app id -u)" = "0" ]; then
  echo "container runs as root" >&2
  exit 1
fi

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    echo "app ready"
    exit 0
  fi
  sleep 1
done
docker logs app >&2
echo "app did not become ready" >&2
exit 1
