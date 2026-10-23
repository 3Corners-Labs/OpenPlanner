# Dependencies

Every runtime dependency must be **actively maintained** and the **de-facto standard** in its
field, with a license in {MIT, BSD, Apache-2.0, PSF} (exceptions by ADR). This table is the
review record; update it in the PR that adds, removes or re-evaluates a package.

Checked on 2026-10-23 against PyPI release history and GitHub activity.

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
| certifi | CA bundle (via httpx) | MPL-2.0 | active | Exception: ADR 0003 |

Rejected: **authlib** (its httpx integration is deprecated and cannot be imported under
`filterwarnings = error`) and **joserfc** (well maintained, but not a de-facto standard). See
ADR 0013.

## Development (Python)

pytest, pytest-asyncio, pytest-cov, ruff, mypy, import-linter, bandit, pip-audit, pip-licenses,
mutmut: all active and standard in their field. They are never shipped in the image.

## Browser tests (npm, CI only)

@playwright/test, @axe-core/playwright (MPL-2.0, required by CLAUDE.md), lighthouse,
chrome-launcher: all active (ADR 0006).

## Containers (development only)

| Image | Purpose | License |
|---|---|---|
| axllent/mailpit | Catch outgoing mail | MIT |
| quay.io/keycloak/keycloak | OIDC provider standing in for PingFederate | Apache-2.0 |
