#!/usr/bin/env bash
# Fail when any job of this workflow run left a deprecation annotation (ADR 0051).
#
# GitHub reports deprecated runtimes, actions, inputs and commands ("Node.js 20 actions are
# deprecated", "set-output is deprecated", ...) only as warning annotations, which nobody reads.
# This turns every one of them into a red build, for whichever module or action causes it.
#
# Runs as the last job of a workflow, after the others (`needs:`), with `checks: read` and
# `actions: read`. Needs GH_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT,
# which Actions provides. Usage: scripts/check-deprecations.sh
set -euo pipefail

: "${GITHUB_REPOSITORY:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"

jobs_url="repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/attempts/$GITHUB_RUN_ATTEMPT/jobs"
found=0
while IFS=$'\t' read -r job_id job_name; do
  messages="$(gh api --paginate "repos/$GITHUB_REPOSITORY/check-runs/$job_id/annotations" \
    --jq '.[] | select(.annotation_level != "notice") | .message' | grep -i 'deprecat' || true)"
  if [[ -n "$messages" ]]; then
    found=1
    while IFS= read -r message; do
      echo "::error title=Deprecated in job '$job_name'::$message"
    done <<<"$messages"
  fi
done < <(gh api --paginate "$jobs_url" --jq '.jobs[] | select(.status == "completed") | [.id, .name] | @tsv')

if [[ "$found" -ne 0 ]]; then
  echo "Deprecation warnings found; update the module that causes them (Renovate PR or by hand)." >&2
  exit 1
fi
echo "No deprecation warnings in this run."
