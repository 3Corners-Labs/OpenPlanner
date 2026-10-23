# OpenPlanner

Lightweight group-booking service for a government contact center. Citizens pick a time slot
for a *group* (a Webex Contact Center queue) on a standalone page per group, optionally
embedded as a Web Component. Five minutes before the start, the service dispatches a task with
a Webex video link to the group's queue via Webex Connect. Personal data is purged 24 hours
after the appointment.

Status: **work package 2 (domain and persistence)**. Scheduling, availability, booking,
cancelling and rescheduling work in the domain and database layers; the public booking pages
and API follow in work package 3. See `docs/HANDOVER.md` §10 for the work packages.

## Quick start

```sh
docker compose up --build
curl http://127.0.0.1:8000/readyz
```

Two containers share one SQLite volume: `web` (FastAPI, runs Alembic migrations on start) and
`worker` (job loop). Both run as non-root with a read-only filesystem.

Local development without Docker (requires [uv](https://docs.astral.sh/uv/)):

```sh
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
make check
```

## Endpoints

| Path | Purpose |
|---|---|
| `GET /` | Home page (nl/en, language switcher). |
| `GET /healthz` | Liveness: the process is up. |
| `GET /readyz` | Readiness: the database answers. `503` otherwise. |
| `GET /openapi.json` | OpenAPI spec (interactive docs are disabled). |

## Where to look next

- `CLAUDE.md` — working agreements, architecture rules, quality gates.
- `CONTRIBUTING.md` — dev setup, tests, PR checklist.
- `docs/HANDOVER.md` — stakeholder design (Dutch).
- `docs/adr/` — decisions.
- `docs/runbook.md` — operating the service.
- `SECURITY.md` — reporting vulnerabilities.
