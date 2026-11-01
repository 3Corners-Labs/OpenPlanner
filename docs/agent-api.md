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
| Scopes | `availability:read` (read groups and free times); `configuration:transfer` (export and import the configuration package of the whole installation, section 6) |
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
GET /api/agent/v1/groups/belastingen/availability?from=2026-11-02&to=2026-11-06&lang=nl
Authorization: Bearer opa_…
```

```json
{
  "group": "belastingen",
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
| 409 | `configuration_installation_in_use`, `configuration_package_version` | The installation has bookings, slots or an active closure; the package is of another schema revision |
| 413 | `configuration_package_too_large` | Above the platform setting *Largest configuration package* |
| 422 | `invalid_request`, `invalid_range` | Bad dates or language; no `X-Passphrase` header |
| 422 | `configuration_passphrase_short`, `configuration_passphrase_wrong`, `configuration_package_invalid` | The passphrase is under 16 characters or wrong; the body is not a package |
| 429 | `rate_limited` | Too many requests (token endpoint: per IP; calls: per client) |
| 503 | `temporarily_unavailable` | The API is switched off (emergency stop) |

## 6. Provision an installation: the configuration package

A pipeline can set up a new installation without a person in the portal (ADR 0089 point 7).
Register a client with the scope `configuration:transfer`. That scope acts on the **whole
installation**, whatever organisation the client is registered under and whatever teams it
is narrowed to; only the superuser registers clients, so grant it to a provisioning client
only, never to a bot.

```http
GET /api/agent/v1/configuration
Authorization: Bearer opa_…
X-Passphrase: <at least 16 characters>
```

Answers `200` with the package as `application/vnd.sqlite3` (a SQLite file, see
`docs/adr/0089-…`): every configuration table of this installation, with its secrets sealed
under the passphrase. No booking, slot or other participant data is in it.

```http
PUT /api/agent/v1/configuration
Authorization: Bearer opa_…
X-Passphrase: <the passphrase the package was exported with>
Content-Type: application/vnd.sqlite3

<the package>
```

Answers `200` with what was imported (counts only):

```json
{"installation": "bron", "app_version": "1.2.3", "created_at": "2026-11-01T12:00:00Z",
 "revision": "0089", "counts": {"organisations": 2, "groups": 2, "identity_providers": 1,
 "api_clients": 1}, "secrets": 3}
```

The rules are the portal's: an import replaces every configuration table in one transaction
and is refused with `409 configuration_installation_in_use` while the installation has a
booking, slot or active closure, and with `409 configuration_package_version` when the package
is of another schema revision. Both calls are audited under `settings.platform.manage` with
the client id as actor and never the passphrase. The passphrase travels in a header, never in
the URL. Access tokens are state, not configuration: after a successful `PUT` every token is
gone, so request a new one; the client itself (and its secret) travels in the package, and so
does the registration of the client that imports it when the package came from the same
installation. `PUT` is a write, so it is refused with `503 maintenance` while maintenance
mode is on (ADR 0038). The host can do the same with `python -m app.cli config
export|import`, and `scripts/install.sh --configuration` (ADR 0089 step 4) uses it.

## 7. What comes next

Changing and cancelling (ADR 0023, next step): the bot sends the caller's phone number and
the wanted change; OpenPlanner sends the participant an email or SMS to confirm, and only that
confirmation changes the booking. The bot gets the same answer whether or not the number has
a booking, so the API cannot reveal who has an appointment.

## 8. Operating it

Rotating secrets, a suspected leak and the emergency stop: `docs/runbook.md`, *API clients of
contact-center bots*. Every client change, token request and refused call is in the audit log.
