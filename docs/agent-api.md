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
| Scopes | `availability:read` (read groups and free times); `configuration:transfer` (export, plan and apply the configuration document of the whole installation, section 6) |
| Teams | Leave empty for every team of the organisation, or tick only the teams the bot serves |
| Allowed networks | Optional: the egress IP ranges of your WxCC region, one per line (CIDR) |
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
| 404 | `group_not_found`, `not_found` | Unknown team or path |
| 409 | `configuration_plan_stale`, `configuration_package_version` | The plan is not the one named by `X-Plan-Hash`, or a plan that deletes came without it; a SQLite package is of another schema revision |
| 413 | `configuration_package_too_large` | Above the platform setting *Largest configuration package* |
| 422 | `invalid_request`, `invalid_range` | Bad dates or language |
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
sealed under the passphrase; without, it holds none. No booking, slot or other participant
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

## 7. What comes next

Changing and cancelling (ADR 0023, next step): the bot sends the caller's phone number and
the wanted change; OpenPlanner sends the participant an email or SMS to confirm, and only that
confirmation changes the booking. The bot gets the same answer whether or not the number has
a booking, so the API cannot reveal who has an appointment.

## 8. Operating it

Rotating secrets, a suspected leak and the emergency stop: `docs/runbook.md`, *API clients of
contact-center bots*. Every client change, token request and refused call is in the audit log.
