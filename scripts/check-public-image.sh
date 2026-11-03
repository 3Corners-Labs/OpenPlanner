#!/usr/bin/env bash
# Fail unless every given image reference can be read without logging in (ADR 0108 point 5).
#
#   scripts/check-public-image.sh ghcr.io/owner/repo-public@sha256:... [more references]
#
# An empty Docker configuration directory means no credentials, so a package that is still
# private answers "unauthorized" here although the release job itself may push to it.
set -Eeuo pipefail

[ "$#" -gt 0 ] || { echo "usage: check-public-image.sh REFERENCE..." >&2; exit 2; }
config=$(mktemp -d)
trap 'rm -rf "$config"' EXIT

status=0
for ref in "$@"; do
  if DOCKER_CONFIG=$config docker manifest inspect "$ref" >/dev/null 2>&1; then
    echo "public: $ref"
  else
    echo "::error::$ref cannot be pulled anonymously: set the package to Public (Package settings, Change visibility), then re-run this job (ADR 0108)"
    status=1
  fi
done
exit "$status"
