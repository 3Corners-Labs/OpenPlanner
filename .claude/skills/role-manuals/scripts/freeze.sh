#!/usr/bin/env bash
# Freeze the settled version of an organisation's role manuals, from a checkout of its release.
#
# Usage, from that checkout: .claude/skills/role-manuals/scripts/freeze.sh <workspace>
#
# Run it after update.sh on the release tag, once a person has reread what changed, settled every
# blocking finding, set the version and date in the sources and written evidence/afhechting.md.
# Every step refuses a workspace inside a checkout.
# 1. records the release the capture shows for the title pages (release.py record);
# 2. builds Word, HTML and PDF (build.mjs), which refuses a source that breaks a rule;
# 3. checks the manuals (check.py) and stops on a blocking finding;
# 4. copies the version into <workspace>/versies/ (release.py freeze): read-only, a checksum per
#    file, a LEESMIJ.md and a zip with its own checksum, and moves the baseline of the next check
#    to the release. It refuses a trial capture, a version that exists and unsettled sources.
# Chrome does not start inside an agent's macOS sandbox: run this in a normal terminal.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../../.." && pwd)"
workspace="$(cd "${1:?usage: freeze.sh <workspace>}" && pwd -P)"

echo "== 1/4 versie vastleggen"
(cd "$repo" && uv run python "$here/release.py" record "$workspace")

echo "== 2/4 bouwen"
node "$here/build.mjs" "$workspace"

echo "== 3/4 controleren"
(cd "$repo" && uv run python "$here/check.py" "$workspace")

echo "== 4/4 bevriezen"
(cd "$repo" && uv run python "$here/release.py" freeze "$workspace")
