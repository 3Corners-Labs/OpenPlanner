#!/usr/bin/env sh
# Makes the release app the author of the commits and tags a release job creates (ADR 0107),
# so the history shows which identity made them. The bot user's id comes from the API, the
# noreply address GitHub links to that bot.
#
# Usage: APP_SLUG=<slug> GH_TOKEN=<the app's token> scripts/release-git-identity.sh
set -eu

slug="${APP_SLUG:?set to the slug of the release app}"
id="$(gh api "/users/${slug}%5Bbot%5D" --jq .id)"
git config user.name "${slug}[bot]"
git config user.email "${id}+${slug}[bot]@users.noreply.github.com"
echo "commits and tags by ${slug}[bot]"
