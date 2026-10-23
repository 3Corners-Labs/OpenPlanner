#!/usr/bin/env sh
# Fails if a runtime dependency has a license outside the allow-list in CLAUDE.md.
# Exceptions are listed in IGNORED with the ADR that approves them.
set -eu

ALLOWED="MIT;MIT License;BSD License;BSD-2-Clause;BSD-3-Clause;Apache-2.0;Apache Software License;Apache-2.0 OR BSD-2-Clause;Apache Software License; MIT License;MIT OR Apache-2.0;MIT AND PSF-2.0;PSF-2.0;Python Software Foundation License"
IGNORED="certifi"  # MPL-2.0, ADR 0003

venv="$(mktemp -d)/venv"
trap 'rm -rf "$(dirname "$venv")"' EXIT

uv venv -q --python 3.12 "$venv"
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  | uv pip install -q --python "$venv/bin/python" -r /dev/stdin
uv pip install -q --python "$venv/bin/python" pip-licenses

"$venv/bin/pip-licenses" --python "$venv/bin/python" --from=mixed \
  --ignore-packages $IGNORED pip-licenses prettytable wcwidth \
  --allow-only="$ALLOWED"
