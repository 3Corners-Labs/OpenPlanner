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
| `GET /readyz` | `200 {"status":"ok"}` | `503`: database unreachable; see `readiness.database_unavailable` in the log. `200 {"status":"degraded"}`: the data volume is short of space; see *Logs and disk space* |
| worker container health | `healthy` | heartbeat older than 120 s: worker stuck or database unreachable (`worker.database_unavailable`) |

Logs are JSON on stdout: `docker compose logs -f web worker`. They rotate at 5 files of 20 MB
per container (see *Logs and disk space*).

`database_busy` errors mean a booking waited longer than 5 s for SQLite's write lock. At the
expected volume (peak 20 bookings/min) this indicates a stuck transaction or a slow disk, not
load: check for long-running admin exports or a full volume.

## Production settings

**Which environment am I in?** `ENVIRONMENT` decides, and it defaults to `prod`, so a forgotten
setting is the safe one. Production refuses to start with development defaults (missing keys,
http addresses, `SIMULATOR_ENABLED=true`). Outside production every admin page shows a striped
bar, "Ontwikkelomgeving" or "Testomgeving" ("not for real appointments of citizens"); in
production there is no bar. An acceptance environment where real citizens book must run with
`ENVIRONMENT=prod`.

The image refuses to start in production (`ENVIRONMENT` defaults to `prod`) without
`MANAGEMENT_TOKEN_KEY` and an `https` `PUBLIC_BASE_URL`. With the bundled TLS proxy (next
section) nothing else is needed. Behind a reverse proxy of your own also set:

- `FORWARDED_ALLOW_IPS=<proxy address>` (read by uvicorn), so rate limits see the visitor's IP
  instead of one shared proxy address. Without it, a single bot can exhaust the booking limit
  for everyone.
- A request body limit on the proxy (for example 64 KB for `/g/*` and `/b/*`).
- No access log on the proxy: paths carry management tokens.

## HTTPS certificate (ADR 0030)

The `proxy` service (Caddy, profile `tls`) gets a certificate from Let's Encrypt over ACME and
renews it on its own; nobody has to install or renew a certificate by hand.

First start:

1. Point DNS for the host name (an `A`/`AAAA` record) at this server. If the domain has a `CAA`
   record, it must allow `letsencrypt.org`.
2. Open ports 80 and 443 from the internet. Port 80 is needed for the ACME challenge and
   redirects everything else to https.
3. `cp proxy.env.example proxy.env` and fill in `TLS_DOMAIN=<host name>` and
   `ACME_EMAIL=<operations mailbox>`. The proxy reads only this file, not `.env`, so it never
   holds the app's secrets and works with `--env-file .env.update` (updater, ADR 0029) too.
   In `.env`: `PUBLIC_BASE_URL=https://<host name>`; leave `FORWARDED_ALLOW_IPS` unset, its
   default is the proxy's fixed address.
4. Trial run first, to stay clear of Let's Encrypt's rate limits: in `proxy.env`
   `ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory`, then
   `docker compose --profile tls up -d` and look for `certificate obtained successfully` in
   `docker compose logs proxy`. Browsers will not trust the staging certificate; that is
   expected.
5. Remove `ACME_CA` (default: Let's Encrypt production), delete the staging certificate with
   `docker compose --profile tls down && docker volume rm <project>_proxy-data`, and start
   again with `docker compose --profile tls up -d`.

Day to day:

- **Renewal** happens inside the running proxy when a third of the lifetime is left (about 30
  days before expiry for 90-day certificates), with retries. A restart is not needed.
- **Check the expiry date** (monthly, or in monitoring; Let's Encrypt sends no expiry mail):
  `echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null | openssl x509 -noout -enddate`.
  Less than 20 days left means renewal is failing: read `docker compose logs proxy` (`tls.obtain`
  / `tls.renew`). Usual causes: port 80 or 443 closed, DNS changed, a `CAA` record.
- **Keep the `proxy-data` volume** and include it in the backup. It holds the certificate, its
  private key and the ACME account; without it every start requests a new certificate, and
  more than 5 per week for the same name are refused.
- **Another ACME CA** (for example ZeroSSL, or a government CA that speaks ACME): set `ACME_CA`
  in `proxy.env` to its directory URL and restart the proxy.
- **Updates from the admin portal** (ADR 0029) restart only `web` and `worker`; the proxy keeps
  running and answers 502 for the seconds `web` is down. Add `--profile tls` to your own
  `docker compose --env-file .env.update up -d` so the proxy stays part of the stack. An organisation-validated (PKIoverheid)
  certificate without ACME is not supported by this profile (ADR 0030).
- **Webhooks**: with a public domain, `WEBHOOK_BASE_URL` can stay empty; Webex and WxCC use
  `PUBLIC_BASE_URL`.
- `web` stays reachable on `127.0.0.1:8000` of the host itself, without the proxy's limits;
  keep shell access to the host restricted.
- `make proxy-check` (also in CI) runs the whole flow against Pebble, Let's Encrypt's test CA.

## Time zones and clock format

Each group has an IANA time zone (default `Europe/Amsterdam`), chosen from a list in the
group settings. Times are stored in UTC and converted only for display, so summer and winter
time need no action: the rules come from the pinned `tzdata` package, which Renovate updates
when IANA publishes a change. A day in the agenda runs from local midnight to local midnight
(23 hours in March, 25 hours in October).

`TIME_FORMAT` sets how clock times are written in the admin portal, booking pages and messages:
`24h` (default, `15:05`), `12h` (`3:05 PM`) or `locale` (24 hours in Dutch, 12 hours in English).
Restart `web` and `worker` after changing it.

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
| Role values | `openplanner-superuser`, `openplanner-org-admin`, `openplanner-group-admin`, `openplanner-auditor` (configurable with `OIDC_ROLE_*`) |

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
0019 (direct call), 0026 (cap on open direct calls).

### 1. Webex Service App

1. In the Webex developer portal, create a **Service App** with exactly these scopes (what
   the code calls; `docs/webhooks.md` has the payloads). Save the client secret when shown.

   | Scope | Used by | Without it |
   |---|---|---|
   | `meeting:admin_schedule_write` | create and delete meetings on behalf of the group's host | no meetings |
   | `meeting:admin_schedule_read` | `GET /meetings`, org-owned `meetings` webhooks | no live meeting status |
   | `meeting:admin_participants_read` | org-owned `meetingParticipants` webhooks | no participant counts (`register-webhooks`: `meetingParticipants/…: refused (rejected_403)`) |
   | `cjp:task_write` | Create Task v2 (work items), Task Messages (mark cancelled) | no work items |
   | `cjp:task_read` | task access the Work Items guide lists with `cjp:task_write` | – |
   | `cjp:user` (optional) | End Task v1 for cancelled bookings, after the v2 mark (ADR 0021) | cancelled tasks are marked cancelled (v2) but stay open until an agent ends them |
   | `cjp:config_read` | list subscriptions and event types (`GET /v2/event-types`), task search (`wxcc-check`) | `register-webhooks` and `wxcc-check` fail with 403 |
   | `cjp:config_write` | create the WxCC subscription (`register-webhooks`) | no WxCC events |

2. A Control Hub administrator authorises it under **Apps → Service Apps**. Org-owned meeting
   webhooks also need that administrator to be a **Webex site admin** of the meeting sites or a
   **Compliance Officer**. The WxCC calls
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
   **One token store only.** A refresh may hand out a new refresh token and retire the old one.
   Run the CLI commands inside the `web` container (same database as `web` and `worker`), never
   with another `DATABASE_URL`: a second database that refreshes from the same
   `WEBEX_REFRESH_TOKEN` leaves the first with a refused token (`webex.token_refresh_refused`),
   and only generating new tokens (step 3) repairs it.
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

#### Matching a group to its queue

OpenPlanner never names a queue: Create Task v2 has no queue field for work items. Three
values per group (**Instellingen → Koppelingen**) select the asset and tell the flow which
queue to use; the meeting link travels inside the work item, so it always reaches the agent who
picks up that booking.

```text
group: WxCC business address ─► destination.id ─► Work Item asset ─► entry point ─► flow
group: WxCC work item-kanaal ─► channel ────────► custom channel (the asset's)
group: Routeringswaarde ─────► flowSettings.openplannerGroup ─► Case ─► Queue Contact ─► queue
                               (empty: the group's web address, e.g. aangifte)
booking: meeting webLink ────► workItemData.meetingLink ─► viewable field + Screen Pop
```

| OpenPlanner group field | Sent as | WxCC item (Control Hub / Flow Designer) | Must match |
|---|---|---|---|
| *WxCC work item-kanaal* | `channel` | Work Item custom channel name | exactly, case included |
| *WxCC business address* | `destination.id` | business address of the Work Item asset on that channel | exactly; unique across assets |
| *Routeringswaarde* (default: web address) | `flowSettings.openplannerGroup` | flow variable `openplannerGroup` (String, settable from outside) and a **Case** branch with that value → **Queue Contact** | one Case branch per value, plus a default branch to a fallback queue |
| – | entry point | Work Item entry point linked to the asset, with the flow active | – |
| – | queue | queue of channel type Work Item, agents' team assigned, multimedia profile with work item capacity | – |

Two layouts:

- **One asset, entry point and flow for all groups** (recommended): every group gets the same
  business address and channel; only the routing value differs. A new group is one Case branch
  and a queue. Groups that share a queue may share a routing value.
- **One asset per group**: each group its own business address, entry point and flow; routing
  happens by address and the routing value is ignored. More configuration, fully separate teams.

How a mismatch shows on the booking's timeline: `CHANNEL_ASSET_UNDEFINED` (business address
matches no asset), `ENTRY_POINT_NOT_FOUND` (no active entry point or flow), `rejected_400` at
creation (schema differs from the table above). A routing value without a Case branch ends in
the default branch: check that queue first. **To verify on the first live test:** that
`openplannerGroup` arrives in the flow variable; Flow Designer may need the variable marked as
overridable by flow settings.

### 4. Webhooks

Set `WEBEX_WEBHOOK_SECRET` and `WXCC_WEBHOOK_SECRET` (random, at least 32 characters, from the
secret store), deploy, check the scopes with `wxcc-check` (section 6), then run once:

```sh
docker compose exec web python -m app.cli register-webhooks
```

It registers the Webex webhooks (meetings started/ended, participants joined/left) **per
group host account** (`hostEmail` filter, from each group's *Video host e-mail*) and the WxCC
subscription (task events) on `WEBHOOK_BASE_URL` (or `PUBLIC_BASE_URL` when that is not set),
and skips what exists. **Run it again after giving a group a new host account.** Unfiltered
webhooks from earlier versions are listed as `stale`: remove them with `webhooks-reset`
(section 8), or OpenPlanner keeps receiving every meeting of the organisation. Webex and WxCC
only call **https on port 443 with a public domain**; in production `WEBHOOK_BASE_URL` must be
https. Afterwards, verify with `webhooks-list` and `webhooks-health` (section 8).

**On a laptop (tunnel).** Keep `PUBLIC_BASE_URL=http://127.0.0.1:8000`, so the Keycloak login
redirect and the links in mails keep working, and give the webhooks their own address:

1. Start a tunnel to the web container: `cloudflared tunnel --url http://localhost:8000`
   (or `ngrok http 8000`). Note the `https://…` address it prints.
2. Set `WEBHOOK_BASE_URL=https://<that address>` in `.env` and restart `web`
   (`docker compose up -d web`). **Koppelingen** now shows the tunnel's webhook addresses.
3. Run `register-webhooks`. A quick tunnel gets a new address on every start: after a
   restart, run `webhooks-reset --yes --reregister` (section 8). It deletes the
   registrations on the old address and registers on the new one.
4. Check delivery with `webhooks-health` (section 8): it sends signed test events through
   the tunnel. A live check: a test meeting's start and the participant counts appear on the
   booking's timeline; `docker compose logs web | grep webhook` shows `webhook.webex` lines
   (`webhook.rejected` means a wrong secret).

**After changing `WXCC_API_BASE_URL`** (for example to the right region), `register-webhooks`
and `webhooks-list` see subscriptions on the new host only; a subscription made earlier
through another host stays. Before the change, run `webhooks-reset --yes` with the old host to
remove it; otherwise remove it with `DELETE /v2/subscriptions/{id}` on the old host, or
events may arrive twice. Webex lists
org-owned webhooks only with `ownedBy=org`; `register-webhooks` asks for that and treats a
`409` (already exists) as `exists`.

**Webex quirk (seen 2026-10-25):** a create that Webex answers with `403` can still leave an
active org-owned webhook behind, which the Service App then cannot delete (`403` from the
webhook engine), and which does not show up for an admin's own token in **List Webhooks**.
Register only after `wxcc-check` shows `GET /webhooks (org-owned)` as `OK`. `webhooks-list`
shows such a webhook as `STALE` (no hostEmail filter), and `webhooks-reset --yes` reports
`refused (rejected_403): Webex quirk` for it and continues with the rest. A left-over
webhook does no harm beyond extra events (unknown meetings are ignored); it goes inactive once
its target stops answering, or Cisco developer support can remove it by id.

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
| `GET /meetings`, `GET /webhooks (org-owned)` | 200 | `meeting:admin_schedule_read`, `meeting:admin_participants_read`; org-owned webhooks also need the authorising admin to be a Webex site admin or Compliance Officer |
| `GET /v2/subscriptions`, `GET /v2/event-types` | 200 | `cjp:config_read`, and the Contact Center administrator authorisation (step 2) |
| `POST /v2/tasks (empty body)` | 400 | `cjp:task_write`, Work Items enabled |
| `POST /v2/tasks/{nil}/messages` | 400 | `cjp:task_write` |
| v1, `WARN` only: `POST /v1/tasks/{nil}/end` | 202 | `cjp:user` (optional) |
| v1, `WARN` only: `GET /v1/tasks` (task search) | 200 | `cjp:config_read` |

A 404 on the WxCC reads means `WXCC_API_BASE_URL` points at the wrong region; the last line,
**WxCC region**, checks it against the configuration service and names the right host (the
task and subscription APIs answer on every regional host, the configuration only on the
organisation's own). Once all lines are `OK`, run `wxcc-config-check` (section 7) and then
`register-webhooks` (section 4). Token expiry and webhook delivery are in `webhooks-health`
(section 8).

### 7. Checking the WxCC routing configuration (`wxcc-config-check`)

Before the first live work item, and after any change in Control Hub or in a group's WxCC
fields, run:

```sh
docker compose exec web python -m app.cli wxcc-config-check
```

It reads the configuration (`cjp:config_read`; read-only) and walks, for every group with a
WxCC channel and business address, the chain from section 3 ("Matching a group to its queue").
It prints names, ids, business addresses and schema keys only; asset webhook secrets and queue
agents are never requested. `FAIL` lines block routing, `WARN` lines are advice; the exit code
is 1 while a `FAIL` remains.

| Line | `FAIL` when | Fix |
|---|---|---|
| WxCC region | the configuration lives in another region | set `WXCC_API_BASE_URL` to the host it names |
| channel | no Work Item channel with the group's channel name | fix the group field or create the channel |
| asset | no asset with the group's business address, or on another channel | fix the group field or the asset |
| asset schema | an OpenPlanner key is missing, or the asset requires a key other than `bookingReference` | make the schema the table in section 3 (`WARN`: a key is not viewable) |
| entry point | no active Work Item entry point with a flow for the asset | link entry point and flow |
| flow | the flow cannot be exported | check the flow is published |
| flow routing | the flow has no `openplannerGroup` variable while several groups share the asset (`WARN` for one group) | add the variable and a Case on it |
| flow branch (`WARN`) | no Case branch with the group's routing value | add the branch, or work items take the default branch |
| flow queues | the flow queues to no known queue, or to a non-Work Item queue | fix Queue Contact |
| queue naming (`WARN`) | no queue in the flow is named after the routing value | rename, or accept the default |
| screen pop (`WARN`) | the flow has no Screen Pop | add one with `meetingLink` |

**Naming rule (advice).** Name each team's queue so that it contains the group's routing value
as a name part; `-`, `_`, spaces and case count as the same. With routing value `aangifte`,
`FIN-BLD_videoafspraak_aangifte_01` matches, `aangiftes_video` does not. The check then shows
per group which queue it will use, and a wrong Case branch stands out. Prefer routing values
without spaces (the web address of the group is a good default).

Once the routing is right, register the webhooks (section 4) and verify them (section 8).

### 8. Listing, cleaning up and testing webhooks

Three commands for registrations at Webex and WxCC. Run them in the `web` container, like the
others: they share its database (one token store, section 1 step 4) and its secrets. Each one
stores its result, which **Koppelingen** in the admin portal shows read-only (time, states,
delivery outcomes). Cleaning up happens only here, on the command line.

```sh
docker compose exec web python -m app.cli webhooks-list
docker compose exec web python -m app.cli webhooks-reset            # dry run
docker compose exec web python -m app.cli webhooks-reset --yes --reregister
docker compose exec web python -m app.cli webhooks-health
```

**`webhooks-list`** lists every org-owned Webex webhook (`ownedBy=org`) and every WxCC
subscription on `WXCC_API_BASE_URL`. Per registration it shows the state, platform, event,
filter, status, whether a secret is set (never the value), target and id. The target has no
query string; for a foreign registration it shows only the host. It ends with `MISSING` lines
for registrations the groups need that do not exist. The exit code is 0 only when everything
is current and nothing is missing.

| State | Meaning | Reasons |
|---|---|---|
| `CURRENT` | OpenPlanner's, on `WEBHOOK_BASE_URL`, for a host account a group uses | – |
| `STALE` | OpenPlanner's (path `/webhooks/webex` or `/webhooks/wxcc`, or named "OpenPlanner …") but no longer right | other address (old tunnel or base URL), no `hostEmail` filter, host account no group uses, event not used, inactive, duplicate |
| `FOREIGN` | another application's | never touched |

**`webhooks-reset`** deletes the `STALE` registrations. Without `--yes` it only prints
`would delete …` lines. With `--yes` it deletes them and prints one line per registration:
`deleted`, `already gone` (404), `refused (rejected_403)` (Webex quirk, section 4; WxCC: needs
`cjp:config_write`) or `failed` (busy or failing; run again). Then it lists again for
**Koppelingen**. `--reregister` (with `--yes`) runs `register-webhooks` afterwards; use it
after a new tunnel address. `CURRENT` and `FOREIGN` registrations are never deleted. The exit
code is 1 when something was refused or failed.

**`webhooks-health`** shows token health (refresh, access and refresh token expiry; `WARN`
two weeks before the refresh token expires). It then sends two test events per endpoint to
`WEBHOOK_BASE_URL`/webhooks/webex and /wxcc: one signed with this environment's secret
(expected `204`) and one with a wrong signature (expected `401`). The events carry ids
(`openplanner-selftest-…`) that belong to no meeting or task, so no booking changes.

| Result | Meaning | Fix |
|---|---|---|
| signed `204`, wrong signature `401` | delivery and verification work | – |
| signed `401` | the secret here differs from the `web` container's (WxCC also: clock) | run the command in `web`; check the secrets |
| `404` | `web` has no secret for this endpoint, or the address reaches something else | set the secret, check `WEBHOOK_BASE_URL` |
| `502`, `530` and other 5xx | proxy or tunnel error | restart the tunnel, then `webhooks-reset --yes --reregister` |
| unreachable | DNS or no tunnel | check `WEBHOOK_BASE_URL` |
| wrong signature `204` | verification is off | stop: report it as a security incident |
| `FAIL WEBHOOK_BASE_URL` | the base is not https | Webex and WxCC deliver to https only |

A passing self-test proves that the address, the tunnel and the secrets work. It does not
prove that Webex or WxCC send events. For that, check `webhooks-list` (all `CURRENT`, nothing
`MISSING`, status `active`) and a test meeting's timeline (section 4, tunnel step 4).

### 9. Checking one group from the admin portal ("Koppeling controleren")

Each group's **Instellingen** page has a card **Koppeling controleren**. Whoever may edit the
group (`group.edit`: the organisation admin, the superuser) can press **Nu controleren**. It
runs, read-only, what sections 6–8 check, but for that one group:

- **Webex:** the host account exists and the Service App may schedule for it.
- **Webex Contact Center:** region, channel, asset, asset schema, entry point, flow
  (`openplannerGroup` and a branch for the routing value), queue, Screen Pop. These are the
  same rules as `wxcc-config-check`.
- **Webhooks:** `WEBHOOK_BASE_URL` is https; this host's webhooks and the WxCC subscription
  exist on the current address; a signed self-test event arrives.

Each line says OK, Waarschuwing, Fout or Overgeslagen, and what to change. The latest result
stays on the page with its time, and every run is audited as `integration.check`. Save
changes first: the check uses the saved settings. It is limited to 6 runs per minute.

### 10. Trying the flows without agents (simulator, not in production)

With `SIMULATOR_ENABLED=true` (off by default; the service refuses to start with it when
`ENVIRONMENT=prod`), an admin booking page (Agenda → a booking) has a card **Simulator (alleen
in test)**. The environment bar then also says "Simulator staat aan". Whoever may edit the
group can:

1. **Nep-meeting en -taak aanmaken** for a booking without them. This sets stand-in ids
   (`simulated-meeting-<id>`, `simulated-task-<id>`, a join link on `.invalid`) and records
   "meeting created" and "task created" with detail `simulated`. Real ids are never replaced.
2. Play events in the order of a call: agent accepts the task, agent joins, citizen joins,
   participant leaves, meeting started or ended, task not picked up, task ended.

Each event is signed with this installation's `WEBEX_WEBHOOK_SECRET` or
`WXCC_WEBHOOK_SECRET` and posted in-process to `/webhooks/webex` or `/webhooks/wxcc`. It takes
the real path (signature, time window, parsing, deduplication, timeline, call status) without
a tunnel. Without a secret the endpoint does not exist and the page says so. Every action is
audited (`simulator.prepare`, `simulator.event`). Without the switch the card is not shown and
the route answers 403.

A quick end-to-end try without Webex: create a group with opening hours and "Direct gesprek
starten", start a call as a citizen (`/g/<slug>/direct`), open the booking in the agenda,
create the stand-ins, and play "agent accepts" and "agent joins". The citizen's waiting page
then shows the matching status.

## Mail

The worker sends mail when `SMTP_HOST` is set (`SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `MAIL_FROM`). Failed deliveries are retried after 1, 5 and 15 minutes and then
marked `failed` in the `jobs` table (`last_error` holds the exception type). Without
`SMTP_HOST`, message jobs stay pending until mail is configured.

## Mail and SMS (ADR 0022)

Each organisation's mail server and SMS service are set by a superuser under **Koppelingen →
Berichtkanalen** (`/admin/organisations/{org}/channels`). Passwords and SMS API keys are
stored encrypted with `SETTINGS_ENCRYPTION_KEY`; without that key they cannot be saved
(the form says so). Changes apply to the next message, no restart needed.

- **Mail.** An organisation without its own SMTP settings uses the installation's server from
  `SMTP_HOST` (`SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `MAIL_FROM`; `SMTP_USER` and `SMTP_USER_PASSWORD` are accepted as aliases); Mailpit in
  development. Use STARTTLS on 587 or TLS on 465 (port 465 always means TLS); plain only for a
  relay on a trusted network. The services refuse to start with a login but neither STARTTLS
  nor TLS.
- **SMS.** Choose the provider (ADR 0028), then enter its API address, API key, the sender
  and the country code for national numbers (31). For **Webex Connect**: create a service
  with an SMS number or alphanumeric sender, copy the service key (service → Messaging API)
  as the API key and the tenant's API host (for example `https://api.us.webexconnect.io`). Organisation admins then add SMS reminders per group under
  **Herinneringen**; the group's form must ask for a phone number.

**Testing mail.** Under **Berichtkanalen**, *Testbericht versturen* sends one message through
the server the worker uses for that organisation (its own settings, else `SMTP_HOST`) and says
what failed: server unreachable, TLS, login, sender or recipient refused. Save changes first;
the test uses the saved settings. Each test is audited (`smtp.test.ok` or `smtp.test.<code>`).

Failed deliveries are retried after 1, 5 and 15 minutes (timeouts, 429, 5xx, SMTP 4xx,
`unreachable`) and then marked `failed` in the `jobs` table (`last_error` holds the exception
type or code). Refusals fail at once: `unauthorized` (wrong SMS API key or SMTP login),
`tls_failed` (wrong security setting for the port, or an invalid certificate), `not_supported`
(the server offers no STARTTLS or login), `sender_refused` (the account may not send from the
sender address), `recipient_refused`, `rejected_<status>` (for example an invalid number or
an SMTP 5xx), `channel_not_configured` (the organisation has no sender on that channel) or
`integration_not_ready` (the settings key is missing). The reminders page of a group warns
when a rule's channel is not configured.

## Management token key

`MANAGEMENT_TOKEN_KEY` (ADR 0011) is required in production. Rotating it invalidates every
link in mails already sent; bookings are unaffected. Rotate only after an incident, and tell the
contact center that citizens may call about broken links.

## Updates from the admin portal (ADR 0029)

With `UPDATE_REPOSITORY=<owner>/<repo>` the worker looks up the latest GitHub release once an
hour, and the superuser sees a banner on every admin page when it is newer than the running
version (`APP_VERSION`, baked into release images). "Update now" and "Restore previous
version" on `/admin/updates` only store a request; the **host updater** carries it out. Without
the updater, requests stay "requested" and the page says so after 10 minutes; deploy by hand as
above.

### Installing the host updater (once per host)

```sh
install -m 755 scripts/updater/openplanner-update.sh /usr/local/sbin/openplanner-update
install -m 644 scripts/updater/openplanner-updater.service scripts/updater/openplanner-updater.timer /etc/systemd/system/
install -d -m 700 /etc/openplanner
install -m 600 scripts/updater/updater.env.example /etc/openplanner/updater.env   # then edit it
# Pin the image that runs now, by digest (after `cosign verify`, see above):
echo "OPENPLANNER_IMAGE=ghcr.io/<owner>/<repo>@sha256:<digest>" > /opt/openplanner/.env.update
systemctl daemon-reload && systemctl enable --now openplanner-updater.timer
```

The host needs `docker` (with compose), `cosign`, `curl` and `flock`. From now on every compose
command on the host passes `--env-file .env.update`, so the pinned digest is what runs.

### What the updater does

| Request | Steps | When something fails |
|---|---|---|
| Update | pull `<image>:<version>`, take its digest, `cosign verify` it (release tag of this repository), note the current digest and Alembic revision in `/var/lib/openplanner-updater/previous.env`, back up the database (`data/backups/request-<id>.db`), pin the new digest, `up -d`, wait up to `HEALTH_TIMEOUT` for `/readyz` and a healthy worker | Before the switch: nothing changed (`image_not_found`, `signature_invalid`, `backup_failed`). After the switch: stop, put the backup back, pin the previous digest, start (`rolled_back`; `failed` with `restore_failed` if that is unhealthy too) |
| Restore previous version (≤ 24 h after an update) | back up, stop, `alembic downgrade <previous revision>` with the newer image, pin the previous digest, start, wait for health. Data entered since the update is kept | Downgrade fails: the newer version starts again (`migration_failed`). Unhealthy: back to the newer version with the backup (`rolled_back`) |

Before each step the updater reports it (`python -m app.cli update-progress ID STEP`: pulling,
verifying, backing_up, migrating, starting, rolling_back; ADR 0048), and at the end the outcome
(`update-report`). The updates page shows the steps live; every step is audited as actor
`host-updater`. A step report that fails (a version from before ADR 0048, or `web` restarting)
does not stop the updater. The updater's own log is in the journal:
`journalctl -u openplanner-updater`.

A request that stays at "Waiting for the updater" is not picked up: check
`systemctl list-timers openplanner-updater.timer` and the journal.

### When the portal shows `failed` with `restore_failed`

Neither version came up. Stop the timer (`systemctl stop openplanner-updater.timer`), look at
`docker compose --env-file .env.update logs web worker`, then put the backup back by hand with
the image from `previous.env`:

```sh
docker compose --env-file .env.update stop web worker
docker compose --env-file .env.update run --rm --no-deps web python -m app.cli restore-db request-<id>
echo "OPENPLANNER_IMAGE=<PREVIOUS_IMAGE>" > .env.update
docker compose --env-file .env.update up -d
```

Backups hold personal data: they stay in the data volume (mode 600) and every updater run
deletes those older than 24 hours (`backup-db --prune`). Never copy them off the host.

## Rollback

Prefer "Restore previous version" in the portal (above) within 24 hours of an update. By hand:
deploy the previous image tag. If the release contained a migration, first run
`docker compose run --rm web alembic downgrade <previous-revision>` with the *new* image, then
deploy the old image. Every migration is reversible (enforced in CI).

## Backup and restore

*TBD (O7).* Planned: Litestream or a daily `sqlite3 .backup`, encrypted, retention = PII term
+ 7 days (HANDOVER §7).

## Rotating secrets

Bootstrap secrets come from environment variables / the secret store; none are baked into the
image. Channel secrets (SMTP password, Connect service key) are replaced by entering a new
value on the organisation's channels page. Changing `SETTINGS_ENCRYPTION_KEY` makes every
stored channel secret and Webex token unreadable: re-enter the channel secrets afterwards
(ADR 0008).

### API clients of contact-center bots (ADR 0023)

Client secrets live only in the contact center's vault; OpenPlanner keeps their hash.

- **Rotate** before the date in **API-koppelingen** (a warning shows 30 days ahead): open the
  client, choose a lifetime, **Nieuw secret maken**, copy the new secret into the WxCC
  configuration. The old secret and every token stop working at once, so do it in a quiet
  moment or coordinate with the contact center.
- **Suspected leak:** open the client and **Uitzetten** (its tokens are revoked at once), then
  rotate and switch it on again.
- **Emergency stop for every bot:** **API-koppelingen** → untick **API aan** → save. Tokens are
  refused immediately. To remove the API from a deployment entirely, unset
  `AGENT_API_ENABLED` and redeploy.
- **Network allow-lists** compare the address uvicorn reports. Behind the reverse proxy that
  is only the caller's address when `FORWARDED_ALLOW_IPS` names the proxy alone (see
  *Production settings*); never set it to `*`, or a forged `X-Forwarded-For` passes the list.
- **Who did what:** the audit log shows client changes (`API-koppelingen beheren`), token
  requests and refused calls, with the client id as actor.

## Emergency closure of a team (ADR 0024)

When a team cannot hold its appointments (outage, illness, closed building):

1. **Close.** Admin portal → the group → tab *Noodsluiting*. Leave *Begin* empty to close now;
   leave *Einde* empty for "until further notice". Write the notice in Dutch and English (plain
   text; no names or other personal data). For every team of an organisation at once:
   *Noodsluiting* in the main menu (organisation admins).
2. **What happens.** Nothing can be booked inside the closure and direct calls are off. Every
   confirmed appointment inside it is put on hold: reminders and the WxCC task are cancelled and
   the citizen gets an email with the notice and a link to choose a new time or cancel. The
   group page shows the notice. Nothing is deleted.
3. **Keep citizens informed.** Change the notice or the end on the same tab and tick *Stuur de
   nieuwe toelichting…* to mail everyone still on hold.
4. **Handle appointments.** In *Opgeschorte afspraken*: move one to a free time (the citizen
   gets the new time and a new link), confirm it again, or cancel it; or do this for all at
   once.
5. **Reopen.** *Opheffen*, optionally confirming again every appointment that can still go
   ahead (before its dispatch lead and outside any closure).
6. **Retention.** Appointments still on hold lose their personal data 30 days after the end of
   the closure. An open-ended closure keeps it until it is lifted, so lift or end it promptly.

## Personal data not removed on time (ADR 0039)

**Retention** in the admin portal (organisation admins, the compliance role and the superuser)
shows per group how many bookings still hold personal data past their purge moment and how
many `purge_pii` jobs failed. Both should be 0. A booking counts as overdue one hour after its
purge moment (slot end + 24 h, cancellation + 24 h, or closure end + 30 days), which leaves
room for the worker and its retries.

| Shown | Likely cause | What to do |
|---|---|---|
| Overdue > 0, failed 0, "last removed" long ago | the `worker` container is not running or not claiming jobs | `docker compose ps worker`, then `docker compose logs worker`; restart it; the backlog clears on its own |
| Failed > 0 | the purge itself failed (database locked or full) | look up the code at **Remove personal data** in the audit log (Problems only); fix the cause and report the code to the developers (below) |
| Overdue > 0 for a suspended booking | the closure was lifted, but no purge job was planned | report it to the developers as a bug (below) |

There is no command yet to re-plan a failed or missing purge; until there is, report it to the
developers with the error code. The nightly sweep from the privacy rules, which would catch such
stragglers by itself, is not built yet either.

## Troubleshooting with the audit log (ADR 0031)

Every admin has an audit log under **Audit log** that lists only their own level: a group admin
sees their groups, an organisation admin sees the organisation and its groups, and the superuser
sees everything, including global events. Besides admin actions it shows **system events**: one
per job the worker ran (confirmation, change, reminder, cancellation, closure messages, video
meeting, WxCC task, purge). Each event shows the outcome, the reason or error code, the attempt
and the channel. Filter on **System events**, **Problems only**, a group, or an appointment
number. A booking id links to that booking's timeline.

| Outcome | Meaning | What to do |
|---|---|---|
| Succeeded | The job did what it should | nothing |
| Skipped, `stale` | The appointment moved or was cancelled after the job was planned | nothing; the new time has its own jobs |
| Skipped, `no_recipient` | No contact details (already purged, or no phone number for an SMS) | nothing, unless it happens for new bookings |
| Will be retried, `<code>` | Failed, tried again after 1, 5, 15 minutes | watch for the next attempt; see the code below |
| Failed, `channel_not_configured` | The organisation has no sender on that channel | set up mail/SMS under Channels (superuser) |
| Failed, other `<code>` | Gave up after the last attempt, or a refusal | look up the code under the integration's section of this runbook |

System events are deleted after `SYSTEM_EVENT_RETENTION_DAYS` (default 90). Admin actions are
kept. Webhook rejections and Webex token-refresh failures are still only in the container logs.

## Log lines and the event schema (ADR 0040)

Every line on stdout is one JSON object in the field names of `openplanner.event/1`
(`docs/audit-events.md`), so a log platform needs no mapping:

| Field | Meaning |
|---|---|
| `@timestamp` | RFC 3339 in UTC with milliseconds, e.g. `2026-10-27T14:03:12.345Z` |
| `log.level` | `debug`, `info`, `warning`, `error`, `critical` |
| `message` | the event key, e.g. `job.sent`, `storage.degraded` |
| `service.name`, `service.version` | `web` or `worker`, and `APP_VERSION` |
| `installation` | `INSTALLATION_NAME`, when set |
| `trace.id` | one id per HTTP request or worker job; the audit events it writes carry the same id |
| `organisation`, `group` | slugs of the tenant the request or job is about, once known |

Lines before this release had `timestamp`, `level` and `event`: change saved searches and
alerts on the log platform to `@timestamp`, `log.level` and `message`. To follow one request
or job, search for its `trace.id`; to see one tenant, filter on `organisation` or `group`.
Public pages under `/g/{slug}` carry only `group`; admin routes carry what they act on.
Timestamps are only as good as the host clock: keep NTP (`timedatectl` shows
`System clock synchronized: yes`) on every host.

## Logs and disk space (ADR 0040)

Three things keep the host from filling up:

- **Container logs rotate.** `docker-compose.yml` gives every service the `json-file` driver
  with `max-size: 20m`, `max-file: 5`, `compress: "true"`: about 100 MB per container at most,
  oldest file dropped first. Other limits: put a `logging:` block in a
  `docker-compose.override.yml`, or set `log-opts` in `/etc/docker/daemon.json`. A log agent
  (Fluent Bit, Splunk forwarder) must read faster than the files rotate.
- **The database gives space back.** The worker returns freed pages to the disk once an hour
  (`database.reclaimed` in the log). A database created before this release is converted once
  at the first start of `web` (`database.auto_vacuum_enabled`); that `VACUUM` needs free space
  about the size of the database. If it logs `database.auto_vacuum_failed`, free some space and
  restart `web`; it tries again on every start.
- **The worker watches the volume.** Every loop it compares the free space of the data volume
  with `STORAGE_LOW_FREE_PERCENT` (default 15) and `STORAGE_CRITICAL_FREE_PERCENT` (default 5),
  and the database (with its WAL) with `DATABASE_MAX_MB` (default: no limit).

| Signal | Where | Meaning |
|---|---|---|
| `storage.degraded`, `warning` | worker log, on change and then hourly | free space below the low limit (`free_space_low`) or database above its maximum (`database_too_large`) |
| `storage.degraded`, `error` | worker log, on change and then hourly | free space below the critical limit (`free_space_critical`) |
| `storage.recovered` | worker log | back to normal |
| *Opslag controleren*, failed | superuser's audit log | the same change of grade, with `free_percent` and `database_mb` |
| `{"status":"degraded"}` | `GET /readyz` (still 200) | either grade; alert on it, it does not take the service out |

What to do: `docker system df` and `du -sh /var/lib/docker/volumes/*` show what grows. Usual
causes are old images (`docker image prune`), logs of containers outside this compose file,
and backups. The database itself shrinks only by retention (`SYSTEM_EVENT_RETENTION_DAYS`) and
the PII purge; never delete rows by hand. Below critical, SQLite writes start to fail and
bookings are refused with `database_busy` or an internal error.

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
- **"All agents busy" while agents are free** (group with a cap on direct calls, ADR 0026):
  the count relies on `task:ended` webhooks. If they are missing, a place frees itself after
  the not-picked-up limit (waiting) or 30 minutes after the booking's end (connected); fix the
  webhooks as below, or clear the cap in the group's settings meanwhile.
- **Webhooks missing** (status not updating while calls happen): check **Koppelingen**, the
  secrets, and re-run `register-webhooks`; WxCC sets a subscription to `inactive` after
  repeated delivery errors.
