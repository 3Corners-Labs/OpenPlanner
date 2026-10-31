#!/usr/bin/env sh
# Refuses a minor or major release tag (vX.Y.0) whose release PR carries no audit (ADR 0078
# point 4). main.yml runs it before it builds or publishes anything, next to the tag checks of
# ADR 0052. A patch release needs no audit. Needs gh with a token that can read pull requests.
#
# Usage: GITHUB_REF_NAME=vX.Y.Z scripts/check-release-audit.sh   (on the tagged commit)
set -eu

tag="${GITHUB_REF_NAME:?set to the release tag}"
case "$tag" in
  v*.*.0) ;;
  *) echo "$tag is a patch release; no audit required (ADR 0078)"; exit 0 ;;
esac

subject="$(git log -1 --format=%s)"
number="$(printf '%s' "$subject" | sed -n 's/.*(#\([0-9][0-9]*\))$/\1/p')"
if [ -z "$number" ]; then
  echo "::error::$tag: the tagged commit is not a squash-merged release PR: $subject"
  exit 1
fi

body="$(gh pr view "$number" --json body --jq .body)"
if ! printf '%s\n' "$body" | grep -q '^## Release audit'; then
  echo "::error::$tag: release PR #$number has no '## Release audit' section (ADR 0078)"
  exit 1
fi
if ! printf '%s\n' "$body" | grep -qE '^Verdict: *pass'; then
  echo "::error::$tag: the audit in release PR #$number does not end with 'Verdict: pass'"
  exit 1
fi
echo "release audit found in PR #$number"
