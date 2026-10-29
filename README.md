# OpenPlanner

**Video appointments with your team, booked in seconds, privacy by default.**

OpenPlanner is an open-source scheduler for Webex video meetings. Your customers pick a free
time on a branded booking page, get a confirmation with a calendar invite, and join the call
from their browser: no account, no app, no login. Your team receives the meeting at the right
moment, in the tool they already work in, and personal data is gone 24 hours after the call.

It is built for teams that serve many people by video: customer service, advisory desks,
helpdesks, intake and support teams. One installation serves several organisations, each with
its own groups, branding, texts and administrators.

## Why OpenPlanner

- **Booking without friction.** One page per team at `/g/{slug}`: a month calendar, the free
  times of a day, and a short form (email, name, optional phone). Move or cancel through a
  personal link; no account needed. Works without JavaScript, fully by keyboard, WCAG 2.2 AA.
- **Webex built in.** Every appointment gets its own Webex meeting. The end user joins from
  the browser; the team member joins as cohost.
- **Lands where your team works.** A few minutes before the start, the call goes as a work
  item to the team's Webex Contact Center queue, so the first available person picks it up. A
  team can also offer "talk now" when it is open, with a cap on how many people wait.
- **Privacy by design.** Personal data lives only on the booking and is purged 24 hours after
  the appointment. No personal data in logs, URLs, jobs or error messages.
- **Your brand, your words, your language.** Colours, logo and fonts per organisation; every
  text editable in the admin portal; Dutch and English out of the box, more locales possible.
  Embed the calendar on your own website as a Web Component, on the origins you allow.
- **Admin with real roles.** Single sign-on through your own identity provider (OIDC), one
  authorisation matrix for every screen and action, and an audit log of every change.
- **Light to run.** Two containers and one SQLite file. Signed images, SBOM, a strict CI
  pipeline, HTTPS with automatic certificates.

## What it does today

- End users book, move and cancel appointments, or start a video call right away when the
  team is open.
- Confirmation, change, reminder and cancellation messages by email (with `.ics`) and SMS.
- Each appointment gets a Webex meeting, handed to the team as a Webex Contact Center work
  item at T−5 (or at once for a direct call).
- Admins manage organisations, groups, staffing, holidays and exceptions, branding, texts,
  integrations and roles, and follow every call in the agenda and the planner.
- Closing a group at short notice suspends bookings and asks end users to pick a new time;
  maintenance mode shows a notice instead of the booking page.
- A website can embed a group's calendar as a Web Component, styled through CSS tokens, on
  the origins its organisation allows (`docs/widget.md`, ADR 0035).
- A machine API lets a contact-center bot (such as the Webex AI Agent) offer free times
  (`docs/agent-api.md`).

On the roadmap: a built-in queue or calendar invite for teams without a contact center
(ADR 0043), changes through the bot API (ADR 0023) and staff provisioning through SCIM
(ADR 0046).

## Quick start (demo)

```sh
cp .env.example .env
docker compose --profile dev up --build -d
docker compose exec web python -m app.cli seed-demo
```

Then open:

- http://127.0.0.1:8000/g/demo — book a video call as an end user (Dutch; English via the
  language switcher).
- http://127.0.0.1:8025 — Mailpit, which catches the confirmation, change, reminder and
  cancellation emails (with calendar attachment and the management link).
- http://127.0.0.1:8000/admin/ — the admin portal. Log in at the development Keycloak with one
  test user per role (`super`, `orgadmin`, `groupadmin`, `auditor`, `outsider`, `norole`; see
  `dev/keycloak/README.md`) to walk the portal as each role.

The `dev` profile adds Mailpit and Keycloak (standing in for your own identity provider);
without it the shape is the production one (web + worker).

On a server with a public domain, `docker compose --profile tls up -d` adds HTTPS with a free
Let's Encrypt certificate that renews itself (ADR 0030; set `TLS_DOMAIN` and `ACME_EMAIL` in
`proxy.env`, see `docs/runbook.md`, "HTTPS certificate").

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
| `POST /webhooks/webex`, `POST /webhooks/wxcc` | Signed events from Webex and Webex Contact Center (only with a secret configured). |
| `GET /admin/` and below | Admin portal (OIDC login, every route bound to one permission of the matrix). |
| `GET /healthz` | Liveness: the process is up. |
| `GET /readyz` | Readiness: the database answers. `503` otherwise. |
| `GET /openapi.json` | OpenAPI spec (interactive docs are disabled). |

## Where to look next

- `docs/architecture.md` — architecture with diagrams: context, containers, layers, domain
  model, runtime flows, security, quality assurance.
- `docs/manual/` — the admin manual, screen by screen (also shown in the portal).
- `docs/widget.md` — embedding the calendar on your website, and its styling tokens.
- `docs/agent-api.md` — connecting a contact-center bot to the machine API.
- `docs/runbook.md` — operating the service.
- `docs/adr/` — decisions.
- `CONTRIBUTING.md` — dev setup, tests, PR checklist.
- `CLAUDE.md` — working agreements, architecture rules, quality gates.
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
