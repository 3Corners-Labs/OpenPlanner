# OpenPlanner

Lightweight group-booking service for a government contact center. Citizens pick a time slot
for a *group* (a Webex Contact Center queue) on a standalone page per group, optionally
embedded as a Web Component. Five minutes before the start, the service dispatches a task with
a Webex video link to the group's queue via Webex Connect. Personal data is purged 24 hours
after the appointment.

Status: **work packages 5 and 6**. Citizens book, move and cancel appointments on the group
page, or start a video call right away when the team is open. Each appointment gets a Webex
meeting; at T−5 (or at once for a direct call) a work item goes to the group's queue in Webex
Contact Center. The citizen joins from the browser; the agent joins as cohost. Admins log in
through OIDC and manage groups, opening hours, exceptions and integrations, and follow every
call in the agenda (authorisation matrix: `docs/authorization.md`). Texts and reminders (WP7c)
follow. A host website can embed a group's calendar with `<gov-booking>` on the origins its
organisation allows (`docs/widget.md`, ADR 0035). See `docs/HANDOVER.md` §10.

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
- http://127.0.0.1:8000/admin/ — the admin portal. Log in at the development Keycloak with one
  test user per role (`super`, `orgadmin`, `groupadmin`, `auditor`, `outsider`, `norole`; see
  `dev/keycloak/README.md`) to walk the portal as each role.

The `dev` profile adds Mailpit and Keycloak (standing in for PingFederate); without it the shape
is the production one (web + worker).

**Installing on a server** (production, test or lab): clone a release tag to
`/home/<user>/openplanner` and run `./scripts/install.sh --mode production`. It installs the
missing host packages, writes `.env` and `proxy.env`, pins the signed release, starts the stack
with HTTPS from Let's Encrypt (ADR 0030) and installs the host updater (ADR 0029). Run it again
to repair a host. See `docs/install.md`.

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
| `GET /g/{slug}` | Booking page of a group: month calendar and the free times of a day. |
| `GET/POST /g/{slug}/book` | Contact form and booking. |
| `GET /b/{token}` | Management page; read-only from the dispatch moment. |
| `GET/POST /b/{token}/cancel`, `/b/{token}/reschedule` | Cancel or move the appointment. |
| `GET /b/{token}/join` | Join page for the video call (opens 15 minutes before the start). |
| `GET/POST /g/{slug}/direct`, `GET /b/{token}/wait` | Start a video call right away; waiting page. |
| `GET /o/{slug}/theme.css`, `GET /o/{slug}/logo` | Branding of an organisation (accent colour, logo). |
| `POST /webhooks/webex`, `POST /webhooks/wxcc` | Signed events from Webex and WxCC (only with a secret configured). |
| `GET /admin/` and below | Admin portal (OIDC login, every route bound to one permission of the matrix). |
| `GET /healthz` | Liveness: the process is up. |
| `GET /readyz` | Readiness: the database answers. `503` otherwise. |
| `GET /openapi.json` | OpenAPI spec (interactive docs are disabled). |

## Where to look next

- `docs/architecture.md` — architecture with diagrams: context, containers, layers, domain
  model, runtime flows, security, quality assurance, roadmap.
- `docs/manual/` — the admin manual, screen by screen (also shown in the portal).
- `CLAUDE.md` — working agreements, architecture rules, quality gates.
- `CONTRIBUTING.md` — dev setup, tests, PR checklist.
- `docs/HANDOVER.md` — stakeholder design (Dutch).
- `docs/adr/` — decisions.
- `docs/install.md` — installing and repairing a host with `scripts/install.sh`.
- `docs/runbook.md` — operating the service.
- `docs/agent-api.md` — connecting a contact-center bot (WxCC AI Agent) to the machine API.
- `SECURITY.md` — reporting vulnerabilities.

## Licence

Copyright © 2026 3Corners Internetworking Services BV ([3corners.nl](https://3corners.nl)) and the
OpenPlanner contributors.

OpenPlanner is licensed under the [European Union Public Licence v1.2](LICENSE) only
(`EUPL-1.2`, ADR 0054). You may use, change, host and sell it; whoever distributes or hosts a
modified version publishes its source under the same terms. `REUSE.toml` declares the licence
and copyright of every file.

Contributions are welcome under the [Contributor Licence Agreement](docs/cla.md); see
`CONTRIBUTING.md`.

"OpenPlanner" and "3Corners" are trademarks of 3Corners Internetworking Services BV. The
licence grants no right to use them: a fork carries another name.
