# 0002. Runtime layout of the skeleton

Status: accepted (2026-10-23)

## Context

Work package 1 sets up the runtime shape for both development and production (one VM, two
containers, SQLite on a volume; HANDOVER B2, B3). Several small choices are not covered by the
handover.

## Decision

1. **Not a package.** The project is run from the source tree (`[tool.uv] package = false`);
   no wheel is built. The image installs only the locked runtime dependencies.
2. **Migrations run in `web` only**, before uvicorn starts. The worker waits for `web` to be
   healthy. This avoids two processes migrating the same SQLite file and keeps the
   two-container shape required by `CLAUDE.md` (no separate migrate service).
3. **Health.** `GET /healthz` (liveness, no dependencies) and `GET /readyz` (database ping;
   `503` with a fixed body on failure, details only as an exception type in the log).
   The worker has no HTTP port: it writes a UTC timestamp to `data/worker.heartbeat` after each
   successful database ping, and its container healthcheck (`python -m app.jobs.healthcheck`)
   fails when the heartbeat is older than `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (default 120 s).
4. **SQLite pragmas** on every connection: `journal_mode=WAL`, `synchronous=NORMAL`,
   `foreign_keys=ON`, `busy_timeout=5000`. The `BEGIN IMMEDIATE` transaction mode for booking is
   added with the booking transaction in work package 2.
5. **Logging.** structlog renders JSON to stdout; stdlib loggers (uvicorn, SQLAlchemy) are
   routed through the same processor chain, so PII masking applies to them too. The uvicorn
   access log is disabled, because request paths will carry management tokens (`/b/{token}`).
   Request logging with route templates instead of raw paths is added with the public API.
6. **Security headers by default.** Every response carries a deny-all CSP, HSTS,
   `X-Frame-Options: DENY`, `nosniff` and `Referrer-Policy: no-referrer`. HTML routes relax CSP
   explicitly when they are added. Swagger UI and ReDoc are disabled (they load scripts from a
   CDN); `/openapi.json` stays available for schemathesis and `docs/api.md`.
7. **CI on GitHub Actions** (`.github/workflows/`). Actions and base images are pinned by tag;
   Renovate pins digests.

## Consequences

- The handover mentions Procrastinate (§9 and work package 4). This conflicts with B3 and
  `CLAUDE.md` (own `jobs` table, no task-queue library). We follow B3; the handover text should
  be corrected by its owner.
- HANDOVER §8 mentions "2 containers + Postgres". The product owner confirmed SQLite
  (2026-10-23). Postgres is not a target; supporting it later needs a new ADR.
- Browser checks, mutation testing and image signing are set up in ADR 0006.
