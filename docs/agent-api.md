# Machine API for contact-center bots

How a bot such as the Webex Contact Center AI Agent connects to OpenPlanner (ADR 0023). The
bot does the conversation, speech recognition and text-to-speech; OpenPlanner answers with
free times. No request or response contains personal data.

The full reference (every endpoint, parameter and error) is generated from the code and
served at `/api/agent/v1/docs` and `/api/agent/v1/openapi.json` while the superuser publishes
it (**API-koppelingen → Documentatie publiceren**).

## 1. Switch it on

1. The deployment sets `AGENT_API_ENABLED=true` (without it the routes do not exist).
2. The superuser opens **API-koppelingen** in the admin portal and ticks **API aan**. Unticking
   it later is the emergency stop: tokens are refused immediately.

## 2. Register the bot

**API-koppelingen → Nieuwe API-client**:

| Field | What to enter |
|---|---|
| Name | The bot's name in Webex, for your own overview |
| Organisation | The one organisation the bot works for (cannot change later) |
| Scopes | `availability:read` (read groups and free times); `reports:read` (the hourly usage figures of the client's groups, [reporting-api.md](reporting-api.md)); `configuration:transfer` (export, plan and apply the configuration document of the whole installation, section 6) |
| Teams | Leave empty for every team of the organisation, or tick only the teams the bot serves |
| Allowed networks | Optional: the egress IP ranges of your WxCC region, one per line (CIDR); an IPv4 address that arrives written as IPv6 (`::ffff:203.0.113.7`) counts as that IPv4 address, and as written it still matches an IPv6 range that lists it (`::ffff:0:0/96`, `::/0`) |
| Secret lifetime | 90, 180 or 365 days; rotate before it ends |

After **Client aanmaken** the page shows the client ID, the client secret and the token URL
**once**. Store the secret in the contact center's vault (for example as the client secret of
an OAuth connector in Control Hub). OpenPlanner keeps only a hash; a lost secret means
**Nieuw secret maken**.

## 3. Get a token

OAuth 2.0 client credentials (RFC 6749 §4.4). HTTP Basic is preferred; the form fields
`client_id` and `client_secret` also work, never both.

```http
POST /api/agent/v1/oauth/token
Authorization: Basic base64(client_id:client_secret)
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

```json
{"access_token": "opa_…", "token_type": "Bearer", "expires_in": 600, "scope": "availability:read"}
```

A token lives 10 minutes. Request a new one when it expires (or on a `401`); a client has at
most five live tokens, older ones are revoked automatically.

## 4. Call the API

```http
GET /api/agent/v1/groups
Authorization: Bearer opa_…
```

```http
GET /api/agent/v1/groups/klantenservice/availability?from=2026-11-02&to=2026-11-06&lang=nl
Authorization: Bearer opa_…
```

```json
{
  "group": "klantenservice",
  "timezone": "Europe/Amsterdam",
  "slots": [
    {"start": "2026-11-02T08:00:00Z", "end": "2026-11-02T08:30:00Z",
     "local_date": "2026-11-02", "local_time": "09:00",
     "label": "maandag 2 november 09:00", "remaining": 2}
  ]
}
```

`label` is ready to be spoken (`lang=nl` or `en`). A range covers at most 62 days.

`GET /api/agent/v1/me` tells the bot which organisation, teams and scopes its token has; use it
to check a new configuration.

## 5. Errors

Every refusal is `{"error": "<code>"}`; it never repeats input.

| Status | `error` | Meaning |
|---|---|---|
| 400 | `invalid_request`, `unsupported_grant_type`, `invalid_scope` | Malformed token request |
| 401 | `invalid_client` | Wrong, expired or disabled credentials, or a network that is not allowed |
| 401 | `invalid_token` | Token missing, expired or revoked: get a new one |
| 403 | `insufficient_scope` | The client may not do this, or not for this team |
| 403 | `configuration_plaintext_disabled` | An export in clear text (`X-Plain-Secrets`) while the host has not allowed it |
| 404 | `group_not_found`, `not_found`, `change_request_not_found` | Unknown team or path, or not a change request of this client |
| 409 | `slot_unavailable` | The requested new time is not free |
| 409 | `configuration_plan_stale`, `configuration_package_version` | The plan is not the one named by `X-Plan-Hash`, or a plan that deletes came without it; a SQLite package is of another schema revision |
| 413 | `configuration_package_too_large` | Above the platform setting *Largest configuration package* |
| 422 | `invalid_request`, `invalid_range` | Bad dates or language |
| 422 | `invalid_contact`, `invalid_appointment_code`, `change_requests_disabled` | A change request with no valid phone number or code, or for an organisation that has not switched change requests on |
| 422 | `configuration_passphrase_short`, `configuration_passphrase_wrong`, `configuration_package_invalid`, `invalid_configuration`, `configuration_out_of_scope`, `configuration_too_many_changes` | The passphrase is under 16 characters or wrong; the body is not a package or a valid document, or names another scope; the plan holds too many changes |
| 429 | `rate_limited` | Too many requests (token endpoint: per IP; calls: per client) |
| 503 | `temporarily_unavailable` | The API is switched off (emergency stop) |

## 6. Provision an installation: the configuration document

A pipeline can set up or change an installation without a person in the portal (ADR 0099, ADR
0089 point 7). Register a client with the scope `configuration:transfer`. That scope acts on
the **whole installation**, whatever organisation the client is registered under and whatever
teams it is narrowed to, and the matrix does not bind it per object; only the superuser
registers clients, so grant it to a provisioning client only, never to a bot.

The configuration is one JSON document (`docs/configuration-document.md`, schema in
`docs/configuration-schema/v1.json`; media type `application/vnd.openplanner.config+json`).

```http
GET /api/agent/v1/configuration
Authorization: Bearer opa_…
X-Passphrase: <at least 16 characters>   (optional)
```

Answers `200` with the document of the platform. With `X-Passphrase` the secrets are in it,
sealed under the passphrase; without, it holds none.
With `X-Plain-Secrets: true` they are in it in clear text instead, with no passphrase; that needs
the host to have set `CONFIGURATION_PLAINTEXT_EXPORT_ALLOWED=true` and is otherwise refused with
`403 configuration_plaintext_disabled`. The response is `Cache-Control: no-store`; keep the file as
private as the secrets. A document with `{"plain": "..."}` secrets applies without a passphrase. No booking, slot or other participant
data is in it.

Read what a document would do before you write it:

```http
POST /api/agent/v1/configuration/plan
Authorization: Bearer opa_…
X-Passphrase: <only if the document holds sealed secrets>

<the document>
```

```json
{"ok": true, "plan_hash": "9c1e…",
 "changes": [{"action": "update", "kind": "group", "path": "organisations[acme].groups[helpdesk]",
              "fields": ["slot_minutes"]}],
 "refusals": []}
```

The plan names kinds, paths and fields, never a value. A plan with a refusal (a value outside
its bounds, an object that bookings or closures still refer to, a document of another scope)
has `"ok": false` and writes nothing.

```http
POST /api/agent/v1/configuration/apply
Authorization: Bearer opa_…
X-Passphrase: <only if the document holds sealed secrets>
X-Plan-Hash: <the plan_hash you reviewed>

<the document>
```

Plans again inside the write lock and writes all or nothing; answers `200`
`{"applied": true, "settings_version": 42, "plan": {…}}`. A plan that deletes anything needs the
`X-Plan-Hash`, and any apply is refused with `409 configuration_plan_stale` when the plan is no
longer that one. A plan with a refusal answers `422` with `"applied": false` and the plan.
A plan above the platform setting *Most changes in one configuration document* is refused
with `422 configuration_too_many_changes`.

`PUT /api/agent/v1/configuration` stays for pipelines that already use it: it is `POST …/apply`
(the document's own `mode`, `X-Plan-Hash` for a plan that deletes). An old SQLite package of ADR
0089 goes to `PUT /api/agent/v1/configuration/package` (`X-Passphrase` required), for one more
release: it is converted to a document in `replace` mode and applied, and the answer is the old
summary (counts only). The call is the confirmation, so what the package leaves out is deleted
unless something refers to it. A package from another schema revision is refused with `409
configuration_package_version`, one with a different table layout, a trigger or a view with `422
configuration_package_invalid`.

**What the scope allows.** `configuration:transfer` is not bound by per-object rights: its
client can change anything a superuser can configure, including API clients, identity providers,
roles and claim values, on an installation in use. Grant it to the pipeline that provisions
installations and to nothing else; every applied change is audited under the client id.

All five calls are audited under the client id, one event per applied change with the plan hash
prefix, kind and natural key, never a value and never the passphrase. The passphrase travels in
a header, never in the URL. `POST` and `PUT` are refused with `503 maintenance` while
maintenance mode is on (ADR 0038); only the export (`GET`) still answers. The host can do the same with `python -m app.cli config
export|plan|apply`, and `scripts/install.sh --configuration` applies a document to a new
installation.

## 7. Cancel or move an appointment: change requests

A bot can ask to cancel or move an appointment for a caller (ADR 0023 step 2). It needs the
scope `bookings:request_change`, and the organisation must have switched on *Change requests by
a bot* (`agent_change_requests_enabled`, organisation settings). The caller names the
appointment by their **phone number and appointment code together**: the code is eight digits,
shown on the participant's booking page and in their confirmation, change and reminder
messages once the organisation switched change requests on.

```http
POST /api/agent/v1/groups/helpdesk/change-requests
Authorization: Bearer opa_…
Content-Type: application/json

{"action": "reschedule", "phone": "06 1234 5678", "code": "1234 5678",
 "start": "2026-01-05T08:30:00Z"}
```

```json
{"id": "chr_…", "action": "reschedule", "start": "2026-01-05T08:30:00Z",
 "status": "pending", "expires_at": "2026-01-01T09:00:00Z"}
```

- `action` is `cancel` (no `start`) or `reschedule` (a `start` from the availability call).
- The answer is always `202 pending`, whether or not the number and code match an appointment
  that can still be changed: say to the caller "if the details match, you will get a message to
  confirm the change". The API never tells whether a number or a code exists.
- On a match the participant gets an email (and an SMS where they agreed to SMS) with a link to
  confirm; nothing changes until they do. A newer request for the same appointment replaces
  the open one, but within *Quiet period after a change request* (default 10 minutes) after a
  request reached it, a new one sends nothing. Cancelling is possible until the start, moving
  until the change deadline. If the bot's client is switched off or loses the scope, or the API
  is switched off, its open requests can no longer be confirmed.
- This API creates no booking, so it has no `flow` field (ADR 0121 step 3a): a confirmed move
  keeps the service flow and revision the appointment was made under.
- A request is deleted *Keep change requests after expiry* days (default 30) after it expired;
  its status is then `404 change_request_not_found`.
- `GET /api/agent/v1/change-requests/{id}` answers `pending`, `confirmed` or `expired`, only to
  the client that asked. A request that matched nothing stays `pending` until `expires_at`
  (*Time to confirm a change request*, default 60 minutes).
- Refused before anything is matched, so they say nothing about an appointment: a malformed
  phone number (`422 invalid_contact`) or code (`422 invalid_appointment_code`), a time that
  is not free (`409 slot_unavailable`), an organisation without change requests
  (`422 change_requests_disabled`).
- Each client may send *Change requests per API client* per hour (default 20), next to the
  general call limit. Every request is in the audit log with the client id and whether it
  matched; the phone number and code are never stored, logged or audited.

## 8. Operating it

Rotating secrets, a suspected leak and the emergency stop: `docs/runbook.md`, *API clients of
contact-center bots*. Every client change, token request and refused call is in the audit log.
