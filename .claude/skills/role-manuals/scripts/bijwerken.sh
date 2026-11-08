#!/usr/bin/env bash
# Bring the role manuals up to date with a new version of the software, in one run.
#
# Usage: scripts/bijwerken.sh <checkout of the software>
#
# 1. starts a fresh stack from the checkout with the verify-openplanner skill;
# 2. keeps the current screenshots in img/vorige/ and captures every id again;
# 3. checks the manuals against the software since the commit of the previous run and replaces
#    labels the software only renamed (check.py --herstel);
# 4. records the commit and version of this run in variabelen.json;
# 5. builds Word, HTML and PDF; the stack is stopped when the script ends.
# Docker and Chrome do not run inside an agent's macOS sandbox: run this in a normal terminal.
# Exit code: that of check.py (1 when a blocking finding is left), else that of the failing step.
set -euo pipefail

workspace="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "${1:?gebruik: scripts/bijwerken.sh <map met de software>}" && pwd)"
stack="$repo/.claude/skills/verify-openplanner/scripts/stack.sh"
previous="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("commit", ""))' "$workspace/variabelen.json")"

# Called by the EXIT trap below, which shellcheck does not follow.
# shellcheck disable=SC2329
stop_stack() {
  (cd "$repo" && "$stack" down) >/dev/null 2>&1 || true
}

echo "== 1/5 stack starten"
(cd "$repo" && "$stack" up)
trap stop_stack EXIT

echo "== 2/5 voorbeeldgegevens laden en opnemen"
export OPENPLANNER_CHECKOUT="$repo"
run_id="$(cat "$repo/.verify-openplanner/current")"
installation="$(sed -n 's/^INSTANCE_ID=//p' "$repo/.verify-openplanner/$run_id/stack.env")"
mkdir -p "$workspace/img/vorige" "$workspace/bewijs/vorige"
find "$workspace/img" -maxdepth 1 -name '*.png' -exec cp -p {} "$workspace/img/vorige/" \;
if [ -f "$workspace/bewijs/opname.json" ]; then
  mv "$workspace/bewijs/opname.json" "$workspace/bewijs/vorige/opname-$run_id.json"
fi
python3 "$workspace/scripts/voorbeeldgegevens.py"
python3 "$workspace/scripts/capture.py" --installation "$installation"

echo "== 3/5 controleren en herstellen"
rc=0
if [ -n "$previous" ]; then
  python3 "$workspace/scripts/check.py" --repo "$repo" --sinds "$previous" --herstel || rc=$?
else
  python3 "$workspace/scripts/check.py" --repo "$repo" || rc=$?
fi

echo "== 4/5 versie vastleggen"
commit="$(git -C "$repo" rev-parse --short=12 HEAD)"
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml" | head -n 1)"
python3 - "$workspace/variabelen.json" "$commit" "$version" <<'PY'
import json
import sys

path, commit, version = sys.argv[1:4]
with open(path) as handle:
    data = json.load(handle)
data["commit"] = commit
data["softwareversie"] = f"{version}, gebouwd uit commit {commit[:7]}"
with open(path, "w") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

echo "== 5/5 bouwen"
node "$workspace/scripts/build.mjs"
python3 "$workspace/scripts/pdf.py"

echo "Klaar. Lees bewijs/controle.md en bekijk de verschilbeelden in bewijs/verschil/."
exit "$rc"
