# 0003. License exception for certifi (MPL-2.0)

Status: accepted (2026-10-23, confirmed by the product owner)

## Context

`CLAUDE.md` allows only MIT, BSD, Apache-2.0 and PSF licenses for dependencies. `httpx`
(required by `CLAUDE.md` as the HTTP client) depends on `certifi`, which is licensed under
MPL-2.0. `certifi` ships Mozilla's CA bundle as an unmodified data file.

## Decision

Allow `certifi` as the single MPL-2.0 runtime dependency. `scripts/check-licenses.sh` lists it
in `IGNORED` with a reference to this ADR; every other package is checked against the
allow-list. Development-only tools (for example `pathspec` via mypy) are not shipped and are
not checked.

## Consequences

MPL-2.0 is a file-level copyleft: obligations apply only if we modify certifi's files, which we
do not. Any further exception needs its own ADR.
