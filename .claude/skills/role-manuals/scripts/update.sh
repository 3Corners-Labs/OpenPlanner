#!/usr/bin/env bash
# Bring an organisation's role manuals up to date with this checkout, in one run.
#
# Usage, from the checkout: .claude/skills/role-manuals/scripts/update.sh <workspace>
#
# 0. builds the manuals without PDF: the one parse of the sources, which refuses a workspace
#    inside a checkout and a source that breaks a rule before anything else runs, and writes the
#    build/manuals.json that check.py reads;
# 1. starts a fresh verification stack from the checkout (verify-openplanner);
# 2. keeps the current screenshots in img/previous/, loads sample data and captures every id;
# 3. collects the changes since the last frozen version per role (release.py notes: the input for
#    the document that says what is new, whose template it puts in sources/ when a reader can
#    notice a change) and builds again without PDF, so the check sees that source;
# 4. checks the manuals against the software since the last frozen version (the baseline commit,
#    which only freeze.sh moves) and writes labels the software only renamed into the sources
#    (check.py --fix);
# 5. records the release the capture shows for the title pages (release.py record: the nearest
#    tag, and whether the image was built from the tag's files; if not, the capture is a trial);
# 6. builds Word, HTML and PDF; the stack is stopped when the script ends.
# Docker and Chrome do not run inside an agent's macOS sandbox: run this in a normal terminal.
# Exit code: that of check.py (1 when a blocking finding is left), else that of the failing step.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../../.." && pwd)"
workspace="$(cd "${1:?usage: update.sh <workspace>}" && pwd -P)"
node "$here/build.mjs" "$workspace" --no-pdf
# A baseline that cannot give release notes stops here, before a capture of half an hour.
(cd "$repo" && uv run python "$here/release.py" baseline "$workspace")
stack="$repo/.claude/skills/verify-openplanner/scripts/stack.sh"

# Its own compose project (one per checkout) and ports, so the stack.sh down of the trap below
# only ever removes this script's containers, never the verification stack of another session.
VERIFY_PROJECT="${VERIFY_PROJECT:-openplanner-manuals-$(printf '%s' "$repo" | cksum | cut -d' ' -f1)}"
VERIFY_WEB_PORT="${VERIFY_WEB_PORT:-8020}"
VERIFY_KC_PORT="${VERIFY_KC_PORT:-8100}"
VERIFY_MAIL_PORT="${VERIFY_MAIL_PORT:-8045}"
export VERIFY_PROJECT VERIFY_WEB_PORT VERIFY_KC_PORT VERIFY_MAIL_PORT

# The stack of another compose project is never stopped by it: it refuses while this checkout has
# a run, the trap stops nothing until a run is recorded, and stack.sh down, given this script's
# VERIFY_PROJECT, refuses a recorded run of any other project. One run per checkout at a time:
# two runs of this script in one checkout share a project and are not supported.
current="${VERIFY_EVIDENCE_ROOT:-$repo/.verify-openplanner}/current"
if [ -e "$current" ]; then
  echo "update.sh: a verification stack is already up; stop it first ($stack down)" >&2
  exit 2
fi

# Called by the EXIT trap below, which shellcheck does not follow.
# shellcheck disable=SC2329
stop_stack() {
  [ -e "$current" ] || return 0
  (cd "$repo" && "$stack" down) >/dev/null 2>&1 || true
}
trap stop_stack EXIT

echo "== 1/6 stack starten"
(cd "$repo" && "$stack" up)

echo "== 2/6 voorbeeldgegevens laden en opnemen"
rm -rf "$workspace/img/previous"
mkdir -p "$workspace/img/previous" "$workspace/evidence/previous"
find "$workspace/img" -maxdepth 1 -name '*.png' -exec cp -p {} "$workspace/img/previous/" \;
if [ -f "$workspace/evidence/state.json" ]; then
  mv "$workspace/evidence/state.json" "$workspace/evidence/previous/state-$(date +%Y%m%d-%H%M%S).json"
fi
node "$here/sample-data.mjs" "$workspace"
node "$here/capture.mjs" "$workspace"

echo "== 3/6 releasenotes verzamelen"
(cd "$repo" && uv run python "$here/release.py" notes "$workspace")
node "$here/build.mjs" "$workspace" --no-pdf

echo "== 4/6 controleren en herstellen"
# Since the commit in variables.json, the last frozen version (check.py's default for --since).
rc=0
(cd "$repo" && uv run python "$here/check.py" "$workspace" --fix) || rc=$?

echo "== 5/6 versie vastleggen"
# The release of the capture's build, not of the HEAD of now.
(cd "$repo" && uv run python "$here/release.py" record "$workspace")

echo "== 6/6 bouwen"
node "$here/build.mjs" "$workspace"

echo "Klaar. Lees evidence/check.md en bekijk de verschilbeelden in evidence/diff/."
exit "$rc"
