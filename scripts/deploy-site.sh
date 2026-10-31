#!/usr/bin/env bash
# Deploy the built website by a git push to the web host's repository (ADR 0087 point 5).
#
# Usage: scripts/deploy-site.sh <dist directory> <version label>
#
# The web host (TransIP) deploys what is pushed to its repository, for openplanner.nl
# `openplannernl@openps.ssh.transip.me:auto.git`. This script clones that repository, replaces
# the site files with the build, commits with the version and the source commit in the
# message, and pushes. The repository keeps every deployed version, so a rollback is a revert.
#
# Reads:
#   SITE_DEPLOY_REMOTE       the repository, user@host:path.git (variable)
#   SITE_DEPLOY_KEY          the private key of the deploy-only key pair (secret), or
#   SITE_DEPLOY_KEY_FILE     the path of a key file instead, for a run from a laptop
#   SITE_DEPLOY_KNOWN_HOSTS  the host's key lines, from ssh-keyscan, checked against the
#                            fingerprints in docs/runbook.md (variable)
#   SITE_DEPLOY_DIR          the folder inside the repository that is the web root; empty is
#                            the repository root (variable, optional)
#   SITE_DEPLOY_BRANCH       the branch to push when the repository is still empty; otherwise
#                            the repository's own default branch is used (variable, optional;
#                            TransIP deploys the "Git production branch" set in its panel)
# TransIP AutoGit needs `.autogit.yml` in the repository root; the script writes it from
# site/deploy/autogit.yml next to the site folder.
# Without SITE_DEPLOY_REMOTE or SITE_DEPLOY_KEY it prints a notice and exits 0, so a fork, a
# pull request or a local run builds and never deploys.
set -euo pipefail

dist="${1:?usage: deploy-site.sh <dist directory> <version label>}"
label="${2:?usage: deploy-site.sh <dist directory> <version label>}"
if [ -z "${SITE_DEPLOY_REMOTE:-}" ] || [ -z "${SITE_DEPLOY_KEY:-}${SITE_DEPLOY_KEY_FILE:-}" ]; then
  echo "::notice::no SITE_DEPLOY_REMOTE or SITE_DEPLOY_KEY, the site is built but not deployed"
  exit 0
fi
: "${SITE_DEPLOY_KNOWN_HOSTS:?SITE_DEPLOY_KNOWN_HOSTS is required with a deploy remote}"
subdir="${SITE_DEPLOY_DIR:-}"
case "$subdir" in
  *..*|/*) echo "::error::SITE_DEPLOY_DIR must be a relative folder without '..' (got '$subdir')"; exit 1 ;;
esac
[ -f "$dist/index.html" ] || { echo "::error::$dist/index.html missing, nothing to deploy"; exit 1; }

autogit="$(cd "$(dirname "$0")/.." && pwd)/site/deploy/autogit.yml"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
umask 077
key="${SITE_DEPLOY_KEY_FILE:-$work/key}"
[ -n "${SITE_DEPLOY_KEY_FILE:-}" ] || printf '%s\n' "$SITE_DEPLOY_KEY" > "$work/key"
printf '%s\n' "$SITE_DEPLOY_KNOWN_HOSTS" > "$work/known_hosts"
export GIT_SSH_COMMAND="ssh -i $key -o UserKnownHostsFile=$work/known_hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes -o LogLevel=ERROR"

git clone --quiet --depth 1 "$SITE_DEPLOY_REMOTE" "$work/repo"
cd "$work/repo"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if ! git rev-parse --quiet --verify HEAD >/dev/null; then
  branch="${SITE_DEPLOY_BRANCH:-main}"
  git checkout --quiet -b "$branch"
fi

# The web root mirrors the build: files removed from site/ disappear from the host too.
target="${subdir:-.}"
mkdir -p "$target"
find "$target" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -R "$dist/." "$target/"
[ ! -f "$autogit" ] || cp "$autogit" .autogit.yml

git add --all
if git diff --cached --quiet; then
  echo "::notice::the site on $SITE_DEPLOY_REMOTE is already this build"
  exit 0
fi
git commit --quiet -m "site: $label" -m "Built from ${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/commit/${GITHUB_SHA:-local} (ADR 0087)."
git push --quiet origin "HEAD:$branch"
echo "::notice::site $label pushed to $SITE_DEPLOY_REMOTE ($branch)"
