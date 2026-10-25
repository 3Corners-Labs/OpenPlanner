# Runbook

Status: skeleton (work package 1). Hosting and backup ownership are open (HANDOVER O7);
sections marked *TBD* are completed in work package 9.

## Deploy

```sh
docker compose pull   # or: docker compose build
docker compose up -d
docker compose ps     # web and worker must report "healthy"
```

`web` applies pending Alembic migrations before it starts serving. The worker starts after
`web` is healthy.

## Verifying the image signature

Only deploy images signed by the `main` workflow of this repository:

```sh
cosign verify ghcr.io/<owner>/<repo>@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/<owner>/<repo>/.github/workflows/main.yml@refs/(heads/main|tags/v.*)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Deploy by digest, not by tag.

## Health

| Check | Healthy | Meaning when unhealthy |
|---|---|---|
| `GET /healthz` | `200 {"status":"ok"}` | web process down or hung |
| `GET /readyz` | `200 {"status":"ok"}` | `503`: database unreachable; see `readiness.database_unavailable` in the log |
| worker container health | `healthy` | heartbeat older than 120 s: worker stuck or database unreachable (`worker.database_unavailable`) |

Logs are JSON on stdout: `docker compose logs -f web worker`.

`database_busy` errors mean a booking waited longer than 5 s for SQLite's write lock. At the
expected volume (peak 20 bookings/min) this indicates a stuck transaction or a slow disk, not
load: check for long-running admin exports or a full volume.

## Production settings

The image refuses to start in production (`ENVIRONMENT` defaults to `prod`) without
`MANAGEMENT_TOKEN_KEY` and an `https` `PUBLIC_BASE_URL`. Behind a reverse proxy also set:

- `FORWARDED_ALLOW_IPS=<proxy address>` (read by uvicorn), so rate limits see the visitor's IP
  instead of one shared proxy address. Without it, a single bot can exhaust the booking limit
  for everyone.
- A request body limit on the proxy (for example 64 KB for `/g/*` and `/b/*`).

## Admin portal login (PingFederate)

Without `OIDC_DISCOVERY_URL` the admin portal is disabled; citizens' pages work regardless.
In production also set `SESSION_SECRET` (at least 32 random bytes); the image refuses to start
without it, and with an `http` discovery URL or without `OIDC_CLIENT_SECRET` (ADR 0013).

In PingFederate, create an OIDC client:

| Setting | Value |
|---|---|
| Client type | confidential, authorization code, PKCE (S256) allowed |
| Client ID | `openplanner-admin` (or set `OIDC_CLIENT_ID`) |
| Redirect URI | `https://<host>/admin/callback` |
| Post-logout redirect URI | `https://<host>/admin/logged-out` |
| ID token signing | RS256, PS256 or ES256 (symmetric algorithms are refused) |
| Scopes | `openid profile email` |
| Claims in the ID token | `roles`, `organisations`, `groups` (multi-valued; names configurable with `OIDC_ROLE_CLAIM`, `OIDC_ORGANISATION_CLAIM`, `OIDC_GROUP_CLAIM`) |
| Role values | `openplanner-superuser`, `openplanner-org-admin`, `openplanner-group-admin` (configurable with `OIDC_ROLE_*`) |

Organisation and group claims carry the slugs shown in the portal (`/g/<group-slug>`). A claim
without the matching role grants nothing. Fill these claims only from sources that admins
manage (directory groups or attributes users cannot edit themselves); a self-editable attribute
would let anyone grant themselves the superuser role.

Roles are read at login and kept in the session for its lifetime (`ADMIN_SESSION_HOURS`,
default 8). Changes need a login of at most `ADMIN_REAUTH_MINUTES` (default 60, ADR 0015):
older sessions pass through PingFederate first, which refreshes the roles. A revoked role
therefore stops granting changes within an hour; reading continues until the session ends.
For an urgent revocation, rotate `SESSION_SECRET` (logs everyone out).

Logout sends `client_id` and `post_logout_redirect_uri` to the `end_session_endpoint`, without
`id_token_hint`. Check during set-up that PingFederate accepts this and ends the SSO session;
otherwise the admin is logged straight back in on the next visit. Check a new set-up by logging in with one account per
role and comparing "Mijn rechten" with `docs/authorization.md`; denied attempts and logins are
in the audit log.

If PingFederate is down, admins cannot log in; booking, reminders and dispatch keep working.
Rotating `SESSION_SECRET` logs every admin out. Rotating the client secret needs the new value
in `OIDC_CLIENT_SECRET` and a restart of `web`.

## Webex meetings and Webex Contact Center

Without a Webex Service App OpenPlanner runs without video and WxCC; bookings keep working.
Contracts and payloads: `docs/webhooks.md`. Decisions: ADR 0017 (video), 0018 (work items),
0019 (direct call).

### 1. Webex Service App

1. In the Webex developer portal, create a **Service App** with exactly these scopes (what
   the code calls; `docs/webhooks.md` has the payloads). Save the client secret when shown.

   | Scope | Used by | Without it |
   |---|---|---|
   | `meeting:admin_schedule_write` | create and delete meetings on behalf of the group's host | no meetings |
   | `meeting:admin_schedule_read` | `GET /meetings`, org-owned `meetings` webhooks | no live meeting status |
   | `meeting:admin_participants_read` | org-owned `meetingParticipants` webhooks | no participant counts |
   | `cjp:task_write` | Create Task v2 (work items), Task Messages (mark cancelled) | no work items |
   | `cjp:task_read` | task access the Work Items guide lists with `cjp:task_write` | – |
   | `cjp:user` (optional) | End Task v1 for cancelled bookings, after the v2 mark (ADR 0020) | cancelled tasks are marked cancelled (v2) but stay open until an agent ends them |
   | `cjp:config_read` | list subscriptions and event types, task search (`wxcc-check`) | `register-webhooks` and `wxcc-check` fail with 403 |
   | `cjp:config_write` | create the WxCC subscription (`register-webhooks`) | no WxCC events |

2. A Control Hub administrator authorises it under **Apps → Service Apps**. The WxCC calls
   need the authorising administrator to hold a **Contact Center administrator** role and the
   organisation to have Work Items (beta) enabled: a 403 on every WxCC probe of `wxcc-check`
   while Webex works means this step or a `cjp:` scope is missing.
3. Back in the developer portal: select the organisation, paste the client secret and
   generate the tokens. Put the client id, client secret and **refresh token** in the secret
   store as `WEBEX_CLIENT_ID`, `WEBEX_CLIENT_SECRET`, `WEBEX_REFRESH_TOKEN`; add
   `SETTINGS_ENCRYPTION_KEY` (ADR 0008). **Adding a scope later** means: add it to the app,
   have it authorised again, generate new tokens and put the new `WEBEX_REFRESH_TOKEN` in the
   secret store; OpenPlanner notices the new token and starts from it (restart `web` and
   `worker`). Old tokens keep their old scopes.
4. OpenPlanner refreshes the access token (14 days) itself and stores rotated tokens encrypted.
   The refresh token lives 90 days: **Koppelingen** in the admin portal warns two weeks ahead.
   Renew it by generating new tokens (step 3) and restarting `web` and `worker`.
5. Check everything with section 6 (`wxcc-check`).

### 2. Webex site settings (Control Hub)

| Setting | Value |
|---|---|
| Guests | may join meetings from the browser without an account |
| Lobby | guests wait in the lobby until admitted (OpenPlanner sets `allowJoinWithLobby`) |
| Virtual backgrounds | on; blur is the default start for everyone |
| Approved backgrounds | upload the central government background (JPG, 1600×900 to 2560×1440); optionally enforce for internal meetings |
| Host accounts | one licensed account per group (or shared), entered per group under **Instellingen → Koppelingen** |

Staff who join signed in with the organisation's account become cohost automatically; check
on a phone that the citizen's browser join works with your site settings (mobile browsers may
offer the Webex app).

### 3. WxCC work items (Control Hub)

Work Items is in beta; the organisation must be enabled by Cisco.

1. **Work Item channel**: e.g. `openplanner-video`. Enter it per group as *WxCC work
   item-kanaal*.
2. **Work Item asset** on that channel with a unique **business address** (e.g.
   `openplanner@<your domain>`; per group as *WxCC business address*) and exactly this schema:

   | Field key | Display name | Required | Viewable |
   |---|---|---|---|
   | `bookingReference` | Afspraak | yes | yes |
   | `firstName` | Voornaam | no | yes |
   | `startTime` | Starttijd | no | yes |
   | `groupName` | Team | no | yes |
   | `meetingLink` | Videogesprek | no | yes |
   | `kind` | Soort (scheduled/direct/cancelled) | no | yes |

3. **Entry point and flow** for the asset. The flow reads the flow setting
   `openplannerGroup` (the group's routing value, by default its web address) and routes with
   **Queue Contact** to the right queue. Add a **Screen Pop** of `meetingLink` so the agent opens
   the meeting in one click (in the Webex app, signed in: cohost). Direct calls can get a higher
   priority with **Set Contact Priority** on `kind = direct`.
4. **Queues** with channel type Work Item, the **multimedia profile** capacity for work items,
   and a **RONA** timeout.
5. Set `WXCC_API_BASE_URL` to your region's API host.

### 4. Webhooks

Set `WEBEX_WEBHOOK_SECRET` and `WXCC_WEBHOOK_SECRET` (random, at least 32 characters, from the
secret store), deploy, check the scopes with `wxcc-check` (section 6), then run once:

```sh
docker compose exec web python -m app.cli register-webhooks
```

It registers the Webex webhooks (meetings started/ended, participants joined/left) and the
WxCC subscription (task events) on `WEBHOOK_BASE_URL` (or `PUBLIC_BASE_URL` when that is not
set), and skips what exists. Webex and WxCC only call **https on port 443 with a public
domain**; in production `WEBHOOK_BASE_URL` must be https.

**On a laptop (tunnel).** Keep `PUBLIC_BASE_URL=http://127.0.0.1:8000`, so the Keycloak login
redirect and the links in mails keep working, and give the webhooks their own address:

1. Start a tunnel to the web container: `cloudflared tunnel --url http://localhost:8000`
   (or `ngrok http 8000`). Note the `https://…` address it prints.
2. Set `WEBHOOK_BASE_URL=https://<that address>` in `.env` and restart `web`
   (`docker compose up -d web`). **Koppelingen** now shows the tunnel's webhook addresses.
3. Run `register-webhooks`. A quick tunnel gets a new address on every start: run it again
   after a restart; the registrations on the old address stay until you delete them in the
   developer portal (Webex) or with `DELETE /v2/subscriptions/{id}` (WxCC).
4. Check delivery: a test meeting's start and the participant counts appear on the
   booking's timeline; `docker compose logs web | grep webhook` shows `webhook.webex` lines
   (`webhook.rejected` means a wrong secret).

Only `/webhooks/*` needs to be reachable through the tunnel; the admin portal is behind OIDC
anyway, but a named tunnel with an ingress rule for that path alone is the tidier option.

### 5. Following calls

- **Groep → Agenda** shows today's bookings with meeting and task state, whether the agent
  joined, participants and a *Niet opgepakt* flag; **Details** shows the timeline.
- **Koppelingen** shows the Service App, token expiry, WxCC and the webhook addresses.

| Timeline reason | Meaning | Action |
|---|---|---|
| `rejected_400` on a task | the work item does not match the asset schema | compare the schema with the table above |
| `ENTRY_POINT_NOT_FOUND` | the asset has no active entry point | link the entry point and flow to the asset |
| `CHANNEL_ASSET_UNDEFINED` | the business address matches no asset | fix the group's business address |
| `ORG_DIGITAL_CONTACT_LIMIT_EXCEEDED` | WxCC's limit of active digital contacts | wait or raise the limit with Cisco |
| `unauthorized` | the Service App token was refused | renew the tokens (section 1) |
| `meeting_missing` (retrying) | the work item waits for the meeting | check Webex reachability and the host account |
| `end_not_possible` `rejected_403` | End Task v1 refused: no `cjp:user` | the task was marked cancelled through v2; end it in Agent Desktop, or add `cjp:user` (section 1) |
| `end_not_possible` other code | WxCC refused End Task (e.g. the task already ended) | check the task in Agent Desktop |

### 6. Checking the Service App (`wxcc-check`)

After creating the Service App, after changing its scopes or authorisation in Control Hub, and
whenever calls fail with `unauthorized` or `rejected_403`, run:

```sh
docker compose exec web python -m app.cli wxcc-check
```

It refreshes the token and makes every kind of call OpenPlanner makes, harmlessly: reads, an
empty work item and an empty task message (WxCC answers 400) and End Task on a task id that
cannot exist (WxCC answers 202: it accepts the request and processes it asynchronously). Per call it prints the verdict, the HTTP status and, on 401/403,
the scope that is probably missing. Work items are v2 (beta) and v2 is primary: Webex and the
v2 probes print `OK`/`FAIL` and decide the exit code (1 while one fails); the v1 probes are
secondary and print `WARN`. It never prints tokens, secrets or response bodies.

| Probe | Expected | On 403 add |
|---|---|---|
| token refresh | ok | (refused: generate new tokens, section 1 step 3) |
| `GET /meetings`, `GET /webhooks` | 200 | `meeting:admin_schedule_read`, `meeting:admin_participants_read` |
| `GET /v2/subscriptions` | 200 | `cjp:config_read`, and the Contact Center administrator authorisation (step 2) |
| `POST /v2/tasks (empty body)` | 400 | `cjp:task_write`, Work Items enabled |
| `POST /v2/tasks/{nil}/messages` | 400 | `cjp:task_write` |
| v1, `WARN` only: `POST /v1/tasks/{nil}/end` | 202 | `cjp:user` (optional) |
| v1, `WARN` only: `GET /v1/subscriptions/event-types`, `GET /v1/tasks` | 200 | `cjp:config_read` |

A 404 on the WxCC reads means `WXCC_API_BASE_URL` points at the wrong region. Once all lines
are `OK`, run `register-webhooks` (section 4).

## Mail

The worker sends mail when `SMTP_HOST` is set (`SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `MAIL_FROM`). Failed deliveries are retried after 1, 5 and 15 minutes and then
marked `failed` in the `jobs` table (`last_error` holds the exception type). Without
`SMTP_HOST`, message jobs stay pending until mail is configured.

## Management token key

`MANAGEMENT_TOKEN_KEY` (ADR 0011) is required in production. Rotating it invalidates every
link in mails already sent; bookings are unaffected. Rotate only after an incident, and tell the
contact center that citizens may call about broken links.

## Rollback

Deploy the previous image tag. If the release contained a migration, first run
`docker compose run --rm web alembic downgrade <previous-revision>` with the *new* image, then
deploy the old image. Every migration is reversible (enforced in CI).

## Backup and restore

*TBD (O7).* Planned: Litestream or a daily `sqlite3 .backup`, encrypted, retention = PII term
+ 7 days (HANDOVER §7).

## Rotating secrets

*TBD.* All secrets come from environment variables / the secret store; none are baked into the
image.

## Webex or WxCC is down

Bookings keep working: they are stored first, and meetings and work items are jobs.

- **Transient failures** (timeouts, 429, 5xx) are retried after 1, 5 and 15 minutes; after
  that the job fails and the timeline shows `integration_error` or `task_failed` with the code.
  The agenda shows the affected bookings.
- **A work item cannot be created at T−5:** the citizen still has the join link and can wait
  in the lobby. Pick the booking from the agenda and contact the citizen another way, or
  re-run the job once WxCC is back (set the job's status to `pending` in the `jobs` table).
- **Direct calls** during an outage show "no agent available" after the group's
  not-picked-up limit and offer to book instead.
- **Webhooks missing** (status not updating while calls happen): check **Koppelingen**, the
  secrets, and re-run `register-webhooks`; WxCC sets a subscription to `inactive` after
  repeated delivery errors.
