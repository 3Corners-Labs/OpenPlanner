# Dependencies

Every runtime dependency must be **actively maintained** and the **de-facto standard** in its
field, with a license in {MIT, BSD, Apache-2.0, PSF} (exceptions by ADR). This table is the
review record; update it in the PR that adds, removes or re-evaluates a package.

Checked on 2026-10-23 against PyPI release history and GitHub activity (tzdata: 2026-10-25).

## Runtime (Python)

| Package | Purpose | License | Maintenance | Note |
|---|---|---|---|---|
| fastapi | Web framework | MIT | very active | Stack (CLAUDE.md) |
| starlette | ASGI toolkit under FastAPI (sessions, static files) | BSD-3 | very active | via FastAPI |
| uvicorn | ASGI server | BSD-3 | active | |
| sqlalchemy | ORM, SQL | MIT | very active | Stack |
| aiosqlite | Async SQLite driver | MIT | active (release 2025-12) | |
| alembic | Migrations | MIT | very active | Stack |
| pydantic, pydantic-settings | Validation, settings | MIT | very active | Stack |
| structlog | JSON logging | MIT / Apache-2.0 | active | Stack |
| httpx | HTTP client (OIDC; Webex later) | BSD-3 | **watch**: no commits since April 2026 | De facto standard. Pydantic's successor `httpx2` is emerging; revisit with the Webex adapters (WP5/WP6) |
| babel | Catalogues, date formatting | BSD-3 | active | |
| jinja2 | Templates | BSD-3 | maintained (Pallets), feature-complete | De facto standard |
| python-multipart | Form parsing for Starlette | Apache-2.0 | active | Required by Starlette forms |
| slowapi (+ limits) | Rate limiting | MIT | active | Named in CLAUDE.md; `limits` is well maintained |
| pyjwt[crypto] | ID-token verification (OIDC) | MIT | very active | De facto JWT library (ADR 0013) |
| cryptography | AES-256-GCM for integration secrets at rest (ADR 0008); also used by PyJWT | Apache-2.0/BSD-3 | very active (PyCA) | De facto Python crypto library; direct dependency since WP5/6 |
| itsdangerous | Signed session cookie (Starlette `SessionMiddleware`) | BSD-3 | maintained (Pallets), feature-complete | Starlette's own choice |
| tzdata | IANA time-zone database for `zoneinfo` (summer/winter time rules) | Apache-2.0 | active (CPython core devs, follows every IANA release) | PEP 615's official fallback; pinned in `uv.lock` and the only source in the image (`PYTHONTZPATH=""`), so DST rules change only through a reviewed Renovate PR |
| markdown-it-py (+ mdurl) | Renders the manual in the portal, raw HTML off (ADR 0033) | MIT | active (executablebooks; release 2026) | De facto CommonMark parser for Python; already in the lock through development tools |
| certifi | CA bundle (via httpx) | MPL-2.0 | active | Exception: ADR 0003 |

Rejected: **authlib** (its httpx integration is deprecated and cannot be imported under
`filterwarnings = error`) and **joserfc** (well maintained, but not a de-facto standard). See
ADR 0013.

## Development (Python)

pytest, pytest-asyncio, pytest-cov, ruff, mypy, import-linter, bandit, pip-audit, pip-licenses,
mutmut: all active and standard in their field. They are never shipped in the image.

## Documentation tooling (local only)

| Package | Purpose | License | Maintenance | Note |
|---|---|---|---|---|
| python-docx | Word version of the architecture plates (`make diagrams`, `scripts/diagrams/`) | MIT | active (1.2.0, 2025) | De facto library for .docx. Added per run with `uv run --with`; not in `uv.lock`, never in CI or the image |

`make diagrams` also calls the draw.io desktop app (Apache-2.0) and Chrome on the developer's
machine; neither is a dependency of the project.

## CI tooling

All kept current by Renovate (ADR 0051): GitHub Actions pinned by SHA, tool versions with a
`# renovate:` line. renovate (AGPL-3.0) runs only as `renovate-config-validator` through `npx`
in `make pins` and as the hosted Renovate app; it is never installed in the project or the
image. jscpd (MIT), shellcheck (GPL-3.0, run as a separate binary through `shellcheck-py` (MIT) and
`uvx`, never linked),
gitleaks (MIT), semgrep (LGPL-2.1, CI container) likewise.

## Browser tests (npm, CI only)

@playwright/test, @axe-core/playwright (MPL-2.0, required by CLAUDE.md), lighthouse,
chrome-launcher: all active (ADR 0006).

## Widget build (npm, CI and image build only)

| Package | Purpose | License | Maintenance | Note |
|---|---|---|---|---|
| esbuild | Bundles `widget/src/` (and its CSS as text) into one ES module, `widget/dist/gov-booking.js` (ADR 0035) | MIT | very active (releases monthly, 0.25.12 checked 2026-10-28) | De facto standard bundler; the stack names it (CLAUDE.md). Runs in the `widget` stage of the Dockerfile and in CI; never in the runtime image. `npm audit` in CI. The image build uses `node:22.20.0-bookworm-slim` (MIT) for that stage only |

The widget itself has no runtime dependencies: vanilla JavaScript, no framework.

## Containers (production)

| Image | Purpose | License | Maintenance | Note |
|---|---|---|---|---|
| caddy, caddy builder (official images) | TLS proxy with automatic ACME certificates and renewal (`--profile tls`) | Apache-2.0 | very active (release every few months, Go stdlib TLS) | ADR 0030; checked 2026-10-26. Own image in `deploy/proxy/` (Caddy rebuilt with current Go, see the Dockerfile), scanned in CI |

## Host packages (installed by `scripts/install.sh`)

Programs on the host that the installer and the host updater run; nothing of them is linked into
or shipped with the image. Kept at their newest version on every installer run, except the
engine (`docs/install.md`). Checked 2026-10-29.

| Package | Purpose | Source | License | Maintenance |
|---|---|---|---|---|
| Docker Engine (`docker-ce`, or Ubuntu's `docker.io`) | runs the containers | Docker's apt repository (key pinned by fingerprint), or Ubuntu | Apache-2.0 | very active |
| Docker Compose plugin (`docker-compose-plugin` / `docker-compose-v2`) | the stack; 2.24+ for `env_file` with `required` | same as the engine | Apache-2.0 | very active |
| cosign | verifies release images before they run (ADR 0006, 0029) | `sigstore/cosign` GitHub releases, `.deb` checked against `cosign_checksums.txt` | Apache-2.0 | very active (OpenSSF) |
| curl, ca-certificates | release checks, health checks, downloads | Ubuntu | curl (MIT-style), MPL-2.0 | very active |
| util-linux (`flock`), gnupg | one updater run at a time; checking Docker's release key | Ubuntu | GPL-2.0+, GPL-3.0+ | very active; OS tools run as separate programs, not linked |

## Containers (development only)

| Image | Purpose | License |
|---|---|---|
| axllent/mailpit | Catch outgoing mail | MIT |
| quay.io/keycloak/keycloak | OIDC provider standing in for PingFederate | Apache-2.0 |
