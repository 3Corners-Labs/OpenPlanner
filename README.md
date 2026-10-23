# OpenPlanner

Lightweight group-booking service for a government contact center. Citizens pick a time slot
for a *group* (a Webex Contact Center queue) on a standalone page per group, optionally
embedded as a Web Component. Five minutes before the start, the service dispatches a task with
a Webex video link to the group's queue via Webex Connect. Personal data is purged 24 hours
after the appointment.

Status: **work package 3, first demo slice**. Citizens can book, view, move and cancel an
appointment on the group page; confirmation, change, reminder and cancellation emails are sent
by the worker. Video links (WP5), WxCC dispatch (WP6) and the admin portal (WP7) follow. See `docs/HANDOVER.md` §10 for the work packages.

## Quick start (demo)

```sh
cp .env.example .env
docker compose --profile dev up --build -d
docker compose exec web python -m app.cli seed-demo
```

Then open:

- http://127.0.0.1:8000/g/demo — book a video call as a citizen (Dutch; English via the
  language switcher).
- http://127.0.0.1:8025 — Mailpit, which catches the confirmation, change, reminder and
  cancellation emails (with calendar attachment and the management link).

The `dev` profile adds Mailpit; without it the shape is the production one (web + worker).

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
| `GET /g/{slug}` | Booking page of a group: slot picker per week. |
| `GET/POST /g/{slug}/book` | Contact form and booking. |
| `GET /b/{token}` | Management page; read-only from the dispatch moment. |
| `GET/POST /b/{token}/cancel`, `/b/{token}/reschedule` | Cancel or move the appointment. |
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
