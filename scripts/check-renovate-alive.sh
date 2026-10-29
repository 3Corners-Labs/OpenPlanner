#!/usr/bin/env bash
# Fail when Renovate has stopped keeping the repository current (ADR 0051).
#
# A Renovate config that nothing runs looks exactly like one that works: no PRs either way.
# This checks that the Dependency Dashboard issue was refreshed recently and that no update PR
# has been left open too long. Run weekly from .github/workflows/freshness.yml.
#
# Needs GH_TOKEN and GITHUB_REPOSITORY. Usage:
#   scripts/check-renovate-alive.sh [max dashboard age in days] [max PR age in days]
set -euo pipefail

: "${GITHUB_REPOSITORY:?}"
max_dashboard_days="${1:-8}"
max_pr_days="${2:-14}"
now="$(date -u +%s)"

dashboard_updated="$(gh issue list --repo "$GITHUB_REPOSITORY" --state open \
  --search 'in:title "Dependency Dashboard"' --json updatedAt --jq '.[0].updatedAt // empty')"
if [[ -z "$dashboard_updated" ]]; then
  echo "::error::No Dependency Dashboard issue: Renovate is not running on this repository."
  exit 1
fi
age_days=$(((now - $(date -u -d "$dashboard_updated" +%s)) / 86400))
if ((age_days > max_dashboard_days)); then
  echo "::error::Dependency Dashboard last updated $age_days days ago (max $max_dashboard_days)."
  exit 1
fi

stale="$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --label dependencies \
  --json number,title,createdAt --jq '.[] | [.number, .createdAt, .title] | @tsv' |
  while IFS=$'\t' read -r number created title; do
    days=$(((now - $(date -u -d "$created" +%s)) / 86400))
    if ((days > max_pr_days)); then echo "#$number ($days days): $title"; fi
  done)"
if [[ -n "$stale" ]]; then
  while IFS= read -r line; do echo "::error::Update PR open too long: $line"; done <<<"$stale"
  exit 1
fi
echo "Renovate ran $age_days day(s) ago; no update PR older than $max_pr_days days."
