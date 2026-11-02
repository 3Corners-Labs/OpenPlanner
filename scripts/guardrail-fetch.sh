#!/usr/bin/env bash
# Reads the live guardrails of the repository and its organisation into one JSON file per
# section, for scripts/guardrail_audit.py (ADR 0109). Needs gh with a token that has
# Administration: read on the repository and the organisation (the audit app in CI, an
# administrator's own login on a laptop). A section that cannot be read is left out, and the
# audit reports it as unreadable.
#
# A read-only app sees less through REST than an administrator: the merge settings come back
# null, a ruleset's bypass actors empty and the collaborator list empty (first audit run,
# 2026-11-02). Those three are read a second way: the merge settings and bypass actors through
# GraphQL, the collaborators as each known user's own role plus the organisation's owners.
# What still cannot be seen stays null, and the audit reports it as unreadable, not as drift.
#
# Usage: scripts/guardrail-fetch.sh OUT_DIR [OWNER/REPO]
set -uo pipefail

out="${1:?usage: guardrail-fetch.sh OUT_DIR [OWNER/REPO]}"
repo="${2:-${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY or pass OWNER/REPO}}"
org="${repo%%/*}"
baseline="${GUARDRAILS_BASELINE:-$(dirname "$0")/../.github/guardrails.json}"
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

# GraphQL's view of the repository: merge settings and every ruleset's bypass actors.
graphql() {
  # shellcheck disable=SC2016 # the $ are GraphQL variables, passed with -F
  gh api graphql -F owner="$org" -F name="${repo#*/}" -f query='
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        mergeCommitAllowed rebaseMergeAllowed squashMergeAllowed deleteBranchOnMerge autoMergeAllowed
        rulesets(first: 50) { nodes { name bypassActors(first: 50) { nodes {
          bypassMode repositoryRoleDatabaseId actor { __typename ... on App { databaseId } }
        } } } }
      }
    }' --jq .data.repository
}

# Each ruleset; bypass actors from GraphQL when REST hides them, null when neither shows them.
rulesets() {
  local ids graph
  ids=$(gh api "repos/$repo/rulesets" --jq '.[].id') || return 1
  graph=$(graphql 2>/dev/null || echo '{}')
  for id in $ids; do gh api "repos/$repo/rulesets/$id" || return 1; done | jq -s --argjson g "$graph" '
    map(. as $r
      | ([$g.rulesets.nodes[]? | select(.name == $r.name) | .bypassActors.nodes] | first) as $via
      | if (($r.bypass_actors // []) | length) > 0 then .
        elif $via != null and ($via | length) > 0 then .bypass_actors = [$via[] | {
            actor_type: (if .actor.__typename == "App" then "Integration" else "RepositoryRole" end),
            actor_id: (.actor.databaseId // .repositoryRoleDatabaseId),
            bypass_mode: (.bypassMode | ascii_downcase)}]
        elif $via != null then .bypass_actors = []
        else .bypass_actors = null end)'
}

# The repository; merge settings from GraphQL when REST leaves them null.
repository() {
  local rest graph
  rest=$(gh api "repos/$repo") || return 1
  graph=$(graphql 2>/dev/null || echo '{}')
  jq -n --argjson r "$rest" --argjson g "$graph" '$r
    | .allow_merge_commit //= $g.mergeCommitAllowed
    | .allow_rebase_merge //= $g.rebaseMergeAllowed
    | .allow_squash_merge //= $g.squashMergeAllowed
    | .delete_branch_on_merge //= $g.deleteBranchOnMerge
    | .allow_auto_merge //= $g.autoMergeAllowed'
}

# Users with more than read access. The list comes back empty for an app, so then each user of
# the baseline is asked for their own role, and the organisation's owners (admin everywhere)
# are added; an empty answer stays empty and the audit reports it as unreadable.
collaborators() {
  local listed
  listed=$(gh api --paginate "repos/$repo/collaborators?affiliation=all" \
    --jq '[.[] | {login, role_name}]' | jq -s 'add // []') || return 1
  if [ "$(jq length <<< "$listed")" -gt 0 ]; then echo "$listed"; return 0; fi
  {
    jq -r '.collaborators // {} | keys[]' "$baseline" 2>/dev/null | while read -r login; do
      gh api "repos/$repo/collaborators/$login/permission" --jq "{login: \"$login\", role_name}" \
        2>/dev/null || true
    done
    gh api --paginate "orgs/$org/members?role=admin" --jq '.[] | {login, role_name: "admin"}' \
      2>/dev/null || true
  } | jq -s 'map(select(.role_name != "read" and .role_name != "none" and .role_name != null))
              | unique_by(.login)'
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
section repository repository
section actions_workflow gh api "repos/$repo/actions/permissions/workflow"
section actions gh api "repos/$repo/actions/permissions"
section environments environments
section collaborators collaborators
section organisation gh api "orgs/$org"
section installations gh api --paginate "orgs/$org/installations" --jq '.installations'
section bypasses gh api --paginate "repos/$repo/rulesets/rule-suites?rule_suite_result=bypass&time_period=day" \
  --jq '[.[] | {actor_name, ref, pushed_at, result}]'
ls "$out"
