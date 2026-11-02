#!/usr/bin/env bash
# Reads the live guardrails of the repository and its organisation into one JSON file per
# section, for scripts/guardrail_audit.py (ADR 0109). Needs gh with a token that has
# Administration: read on the repository and the organisation (the audit app in CI, an
# administrator's own login on a laptop). A section that cannot be read is left out, and the
# audit reports it as unreadable.
#
# Usage: scripts/guardrail-fetch.sh OUT_DIR [OWNER/REPO]
set -uo pipefail

out="${1:?usage: guardrail-fetch.sh OUT_DIR [OWNER/REPO]}"
repo="${2:-${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY or pass OWNER/REPO}}"
org="${repo%%/*}"
mkdir -p "$out"

# One section: run the command, keep its output only when it succeeded.
section() {
  local name=$1; shift
  if "$@" > "$out/$name.json.tmp" 2> "$out/$name.err"; then
    mv "$out/$name.json.tmp" "$out/$name.json"
    rm -f "$out/$name.err"
  else
    rm -f "$out/$name.json.tmp"
    echo "::warning::cannot read $name: $(head -c 200 "$out/$name.err")"
  fi
}

rulesets() {
  local ids
  ids=$(gh api "repos/$repo/rulesets" --jq '.[].id') || return 1
  for id in $ids; do gh api "repos/$repo/rulesets/$id" || return 1; done | jq -s .
}

environments() {
  local names
  names=$(gh api "repos/$repo/environments" --jq '.environments[].name') || return 1
  for name in $names; do
    gh api "repos/$repo/environments/$name" --jq '{name, deployment_branch_policy}' || return 1
    gh api "repos/$repo/environments/$name/deployment-branch-policies" \
      --jq "{name: \"$name\", branches: [.branch_policies[] | {name, type}]}" || return 1
  done | jq -s 'group_by(.name) | map(add)'
}

section rulesets rulesets
section repository gh api "repos/$repo"
section actions_workflow gh api "repos/$repo/actions/permissions/workflow"
section actions gh api "repos/$repo/actions/permissions"
section environments environments
section collaborators gh api --paginate "repos/$repo/collaborators?affiliation=all" \
  --jq '[.[] | {login, role_name}]'
section organisation gh api "orgs/$org"
section installations gh api --paginate "orgs/$org/installations" --jq '.installations'
section bypasses gh api --paginate "repos/$repo/rulesets/rule-suites?rule_suite_result=bypass&time_period=day" \
  --jq '[.[] | {actor_name, ref, pushed_at, result}]'
ls "$out"
