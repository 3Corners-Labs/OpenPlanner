# OpenPlanner

[openplanner.nl](https://openplanner.nl)

**Video appointments that land in your contact center. Open source, privacy by default.**

OpenPlanner is an open-source scheduler for Webex video meetings. Participants pick a free
time on a branded booking page, confirm it by email, get a calendar invite, and join the call
from their browser: no account, no app, no login. Your team receives the meeting at the right
moment, in the tool they already work in, and personal data is gone 24 hours after the call.

It is built for teams that serve many people by video: customer service, advisory desks,
helpdesks, intake and support teams. One installation serves several organisations, each with
its own groups, branding, texts and administrators.

## Why OpenPlanner

- **Booking without friction.** One page per team at `/g/{slug}`: a month calendar, the free
  times of a day, and a short form (first name, last name, email, optional phone, plus up to
  a few questions of the group's own from a closed catalogue). The participant confirms by
  email; move or cancel through a personal link, no account needed. Works without JavaScript,
  fully by keyboard, WCAG 2.2 AA.
- **Webex built in.** Every appointment gets its own Webex meeting, with a trial call
  beforehand. The participant joins from the browser; the team member joins as cohost.
- **Lands where your team works.** A few minutes before the start, the call goes as a work
  item to the team's Webex Contact Center queue, so the first available person picks it up.
  A team without a contact center uses OpenPlanner's own queue, optionally with a calendar
  invite to the team's mailbox. A team can also offer "talk now" when it is open, with a cap
  on how many people wait.
- **Privacy by design.** Personal data lives only on the booking and is purged 24 hours after
  the appointment. No personal data in logs, URLs, jobs or error messages.
- **Your brand, your words, your language.** Colours, logo and fonts per organisation; every
  text editable in the admin portal; Dutch and English out of the box, more locales possible.
  Embed the calendar on your own website as a Web Component, on the origins you allow.
- **Admin with real roles.** Single sign-on with a second factor, through the bundled
  Keycloak (staff managed in the portal) or each organisation's own identity provider (OIDC),
  chosen by email domain. One authorisation matrix for every screen and action, and an audit
  log of every change.
- **Light to run.** Two containers and one SQLite file. Signed images, SBOM, a strict CI
  pipeline, HTTPS with automatic certificates.

## What it does today

- Participants book, confirm by email, move and cancel appointments, try their camera and
  microphone in a trial call, or start a video call right away when the team is open.
- Confirmation, change, reminder and cancellation messages by email (with `.ics`) and SMS;
  the portal checks each sender domain's SPF, DKIM and DMARC.
- Each appointment gets a Webex meeting, handed to the team as a Webex Contact Center work
  item at T−5 (or at once for a direct call), or placed in OpenPlanner's own queue.
- Admins manage organisations, groups, staffing, holidays and exceptions, branding, texts,
  booking questions, integrations and staff, and follow every call in the agenda, the planner
  and a live overview with alerts. A booking's troubleshooting page shows what happened with
  every provider.
- The superuser connects log platforms (syslog, Splunk, Elastic, HTTP), picks the update
  channel and installs updates from the portal, and moves a whole configuration to a new
  installation as one encrypted package.
- Closing a group at short notice suspends bookings and asks end users to pick a new time;
  maintenance mode shows a notice instead of the booking page.
- A website can embed a group's calendar as a Web Component, styled through CSS tokens, on
  the origins its organisation allows (`docs/widget.md`, ADR 0035).
- A machine API lets a contact-center bot (such as the Webex AI Agent) offer free times
  (`docs/agent-api.md`).

On the roadmap: other contact-center platforms on the same adapter layer (ADR 0032),
contact-center health checks with alerts to a Webex space and a fallback per group (ADR 0082),
subjects and routing attributes per group (ADR 0081), the rebooking assistant (ADR 0057),
changes through the bot API (ADR 0023) and staff provisioning through SCIM (ADR 0046). What is
built of every decision is in `docs/adr-status.md`.

## Quick start (demo)

```sh
cp .env.example .env
docker compose --profile dev up --build -d
docker compose exec web python -m app.cli seed-demo
```

Then open:

- http://127.0.0.1:8000/g/demo — book a video call as a participant (Dutch; English via the
  language switcher).
- http://127.0.0.1:8025 — Mailpit, which catches the confirmation, change, reminder and
  cancellation emails (with calendar attachment and the management link).
- http://127.0.0.1:8000/admin/ — the admin portal. Log in at the development Keycloak with one
  test user per role (`super`, `orgadmin`, `groupadmin`, `auditor`, `agent`, `outsider`, `norole`; see
  `dev/keycloak/README.md`) to walk the portal as each role.

The `dev` profile adds Mailpit and Keycloak (standing in for your own identity provider);
without it the shape is the production one (web + worker).

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
| `GET /b/{token}` | The link from an email: checks the token, sets a participant cookie and redirects to `/b/m/{ref}` (ADR 0093). |
| `GET /b/m/{ref}` | Management page; read-only from the dispatch moment. |
| `GET/POST /b/m/{ref}/confirm` | Confirm the booking (ADR 0056). |
| `GET/POST /b/m/{ref}/cancel`, `/b/m/{ref}/reschedule` | Cancel or move the appointment. |
| `GET/POST /b/m/{ref}/test` | Trial call on Webex before the appointment (ADR 0071). |
| `GET /b/m/{ref}/join` | Join page for the video call (opens 15 minutes before the start). |
| `GET/POST /g/{slug}/direct`, `GET /b/m/{ref}/wait` | Start a video call right away; waiting page. |
| `GET /o/{slug}/theme.css`, `GET /o/{slug}/logo` | Branding of an organisation (accent colour, logo). |
| `/api/v1/widget/{org}/{group}/…` | Embedded widget: group, availability, booking (allowed origins only). |
| `/api/agent/v1/…` | Machine API for contact-center bots (OAuth client credentials, `docs/agent-api.md`). |
| `GET /api/v1/instance` | The installation's public identity, for the round-trip check of the public address (ADR 0086). |
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
- `docs/logging.md` — log levels, the record, the trace points and where the lines go.
- `docs/agent-api.md` — connecting a contact-center bot to the machine API.
- `docs/authentication.md` — how admins, bots and participants prove who they are, and how that is withdrawn.
- `docs/install.md` — installing and repairing a host with `scripts/install.sh`.
- `site/` — the public website, openplanner.nl, built from the repository (ADR 0087).
- `docs/runbook.md` — operating the service.
- `docs/asvs.md` — for each requirement of the ASVS 5.0.0 level 2 audit that was not fully accepted: where the measure is now, and whether the gap is closed, accepted on purpose or open.
- `docs/assurance/` — checklists for the parties that must give evidence we cannot (identity provider, hosting, Cisco, data controller) and the scope for the penetration test.
- `docs/adr/` — decisions; `docs/adr-status.md` — what of each one is built.
- `CONTRIBUTING.md` — dev setup, tests, PR checklist.
- `CLAUDE.md` — working agreements, architecture rules, quality gates.
- `SECURITY.md` — reporting vulnerabilities.

## Disclaimer

OpenPlanner is provided as it is, without warranty of any kind and without liability of
3Corners or the contributors for damage from its use, as sections 7 and 8 of the EUPL-1.2 say.
Whoever installs and runs it is responsible for its configuration, hosting and use, and for
compliance with the law that applies to them (privacy, accessibility, retention, the rules of
their sector). The documentation, the website and the standards tables describe what the
software does; they are no legal or compliance advice and no guarantee that an installation
meets a standard. See also `SECURITY.md` for reporting vulnerabilities.

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
