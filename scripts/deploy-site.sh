#!/usr/bin/env bash
# Deploy the built website to the web server by rsync over SSH (ADR 0087 point 5).
#
# Usage: scripts/deploy-site.sh <dist directory>
#
# Reads SITE_DEPLOY_HOST, SITE_DEPLOY_USER, SITE_DEPLOY_PATH, SITE_DEPLOY_KEY (the private key)
# and SITE_DEPLOY_KNOWN_HOSTS (the server's host key line). Without the host it prints a notice
# and exits 0, so a fork, a pull request or a local run builds and never deploys. The key is
# written to a temporary file with mode 600 and removed afterwards. `--delete` keeps the server
# equal to the build: a page removed from site/ disappears from the server.
set -euo pipefail

dist="${1:?usage: deploy-site.sh <dist directory>}"
if [ -z "${SITE_DEPLOY_HOST:-}" ]; then
  echo "::notice::no SITE_DEPLOY_HOST, the site is built but not deployed"
  exit 0
fi
: "${SITE_DEPLOY_USER:?}" "${SITE_DEPLOY_PATH:?}" "${SITE_DEPLOY_KEY:?}" "${SITE_DEPLOY_KNOWN_HOSTS:?}"
case "$SITE_DEPLOY_PATH" in
  /*/*) ;;
  *) echo "::error::SITE_DEPLOY_PATH must be an absolute path below the root (got '$SITE_DEPLOY_PATH')"; exit 1 ;;
esac

ssh_dir="$(mktemp -d)"
trap 'rm -rf "$ssh_dir"' EXIT
umask 077
printf '%s\n' "$SITE_DEPLOY_KEY" > "$ssh_dir/key"
printf '%s\n' "$SITE_DEPLOY_KNOWN_HOSTS" > "$ssh_dir/known_hosts"

rsync -az --delete --no-perms --no-owner --no-group --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  -e "ssh -i $ssh_dir/key -o UserKnownHostsFile=$ssh_dir/known_hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes" \
  "$dist/" "$SITE_DEPLOY_USER@$SITE_DEPLOY_HOST:$SITE_DEPLOY_PATH/"
echo "::notice::site deployed to $SITE_DEPLOY_HOST:$SITE_DEPLOY_PATH"
