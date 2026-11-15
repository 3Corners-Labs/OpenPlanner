#!/usr/bin/env sh
# An optional, occasional review of everything since the last release by a second model
# (ADR 0128; it was a required step for a minor or major release under ADR 0078 point 4). No
# workflow runs it and no check waits for it: whoever wants a second opinion runs it on their
# own machine, with the Codex CLI installed and signed in to their own account. It reuses the
# review mode of the Codex CLI exactly as the /codex skill runs it: read-only sandbox, high
# reasoning effort, no prompt of our own. The findings may go into the release PR under
# "## Release audit"; nothing reads a verdict there.
#
# Usage: scripts/release-audit.sh [BASE]
#   BASE  the ref to review against; default: the last release tag (candidates excluded).
set -eu

if ! command -v codex >/dev/null 2>&1; then
  echo "release-audit: the Codex CLI is not installed; see docs/runbook.md, Releasing a version" >&2
  exit 2
fi

base="${1:-$(git describe --tags --abbrev=0 --match 'v[0-9]*' --exclude 'v*-rc.*')}"
echo "release audit: reviewing the changes since $base in a read-only sandbox" >&2
exec codex review --base "$base" \
  -c 'sandbox_mode="read-only"' \
  -c 'model_reasoning_effort="high"' \
  -c 'web_search="cached"' </dev/null
