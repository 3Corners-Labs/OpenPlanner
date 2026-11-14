# shellcheck shell=sh disable=SC2034 # the scripts that source this file use the values
# The fixed addresses of the containers on one host that another container reaches (ADR 0125
# step 2): never a name, which this host's DNS could answer while the container is down.
# docker-compose.yml gives them to the services, and app/domain/web_transport.py holds the
# identity provider's for the start-up check; tests/test_docker_compose.py keeps all three equal.
# Sourced, never run, by scripts/install.sh, scripts/render-keycloak-realm.sh and
# scripts/check-proxy.sh: the host has no Python to read the application's copy.

# The bundled identity provider on the edge network (the proxy's IDP_UPSTREAM, the login and the
# staff directory of web and worker).
# not a pin: a fixed IPv4 address, not a version
BUNDLED_IDP_ADDRESS=172.31.240.4
BUNDLED_IDP_PORT=8080
# Its database on the internal network idp-store (Keycloak's KC_DB_URL_HOST).
# not a pin: a fixed IPv4 address, not a version
BUNDLED_IDP_DB_ADDRESS=172.31.240.18
