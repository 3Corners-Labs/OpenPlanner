#!/usr/bin/env sh
# Start the development Keycloak (dev/keycloak/, ADR 0013) from docker-compose.yml, so CI uses
# the same image, realm and hostname settings as local development. Waits until the realm's
# discovery document answers, then prints the Docker network Keycloak is on: start the app
# container on that network and it reaches Keycloak as http://keycloak:8080.
# Usage: network="$(scripts/run-keycloak-container.sh)"
set -eu

docker compose --profile dev up -d keycloak >&2
container="$(docker compose --profile dev ps -q keycloak)"
discovery="http://127.0.0.1:8080/realms/openplanner/.well-known/openid-configuration"

for _ in $(seq 1 90); do
  if curl -fsS "$discovery" >/dev/null 2>&1; then
    echo "keycloak ready" >&2
    docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$container"
    exit 0
  fi
  sleep 2
done
docker logs "$container" >&2
echo "keycloak did not become ready" >&2
exit 1
