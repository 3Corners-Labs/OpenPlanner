# 0023. Machine API for contact-center bots (WxCC AI Agent)

Status: accepted (2026-10-25); step 2 (change requests) decided by the product owner on 2026-11-09. First step of open point O11. Built: see *Build status* and `docs/adr-status.md`.

## Context

The Webex Contact Center AI Agent (chat and voice bots) should be able to help participants with
their appointments. Webex CC does the conversation, speech recognition and text-to-speech; we
deliver only an API on the planning (HANDOVER §11, O11). The participant identifies with the
phone number of the booking and confirms every change through a message to email or SMS
that leads to the existing management page.

This is the first entry point into OpenPlanner that is neither a participant in a browser nor an
admin with an IdP login. It must not become a back door: it may never do more than a
participant with the management link can, it must be switchable off at once, and every use
must be visible to the superuser.

## Decision

1. **A separate API, `/api/agent/v1`.** Its own FastAPI sub-application with its own OpenAPI
   spec, JSON errors and versioning; the public `/api/v1` is not affected.
2. **Two switches, both off by default.** `AGENT_API_ENABLED` in the environment decides
   whether the routes exist at all (a deployment opts in). The superuser's switch in the admin
   portal turns the API off at once without a redeploy (emergency stop); it also refuses
   token requests. Publishing the API documentation is a third, separate switch.
3. **OAuth 2.0 client credentials (RFC 6749 §4.4), issued by OpenPlanner.** The superuser
   creates an API client per bot in the admin portal. The client secret is shown once,
   stored only as its SHA-256 (it has 256 random bits; a slow hash adds nothing), and expires
   (90, 180 or 365 days). Clients authenticate with HTTP Basic or form fields at
   `POST /api/agent/v1/oauth/token` and receive an opaque bearer token valid for 10 minutes,
   also stored only as its SHA-256. Opaque tokens can be revoked at once; nothing is signed
   that could outlive a revocation. Secrets and tokens carry a prefix (`ops_`, `opa_`) so
   secret scanners recognise them.
   *Not chosen now:* tokens from the IdP (PingFederate client credentials). That moves client
   management out of our audit log; it can be added later as a second issuer behind the same
   dependency.
4. **Least privilege per client.** A client belongs to one organisation, optionally to a
   subset of its groups, and holds OAuth scopes. The scopes are part of the authorisation
   matrix (`API_SCOPES` in `app/domain/authorization.py`, ADR 0014) and appear in
   `docs/authorization.md`. Optional network allow-list (CIDR) per client, for the published
   egress ranges of the WxCC region.
5. **No back door by construction.** The machine API never returns personal data, never
   changes a booking by itself, and never accepts a management token. Changing or cancelling
   (next step) creates a *change request*; only the participant's confirmation through their own
   email or SMS applies it. The superuser has no route that impersonates a client.
6. **Guardrails.** Rate limits: token endpoint per IP, API calls per client. At most
   `MAX_ACTIVE_TOKENS` live tokens per client (older ones are revoked). Every token issue,
   every refused token request and every denied API call is an audit event (client id,
   reason, never the secret or token); every client change too. Responses carry
   `Cache-Control: no-store`. Disabling a client or rotating its secret revokes its tokens in
   the same transaction.
7. **Managed by the superuser only.** New permission `api_clients.manage` (global level,
   superuser), with a fresh login for every change (ADR 0015).

## Endpoints

| Step | Endpoint | Scope |
|---|---|---|
| this ADR | `POST /oauth/token` | – |
| this ADR | `GET /me` (who am I, which scopes and groups) | any valid token |
| this ADR | `GET /groups`, `GET /groups/{slug}/availability` | `availability:read` |
| step 2 | `POST /groups/{slug}/change-requests` (phone number + appointment code + reschedule/cancel) | `bookings:request_change` |
| step 2 | `GET /change-requests/{id}` (pending, confirmed, expired) | `bookings:request_change` |

The change-request flow (next step) answers the same way whether or not the phone number
matches a booking, so the API cannot be used to find out who has an appointment. It depends
on SMS (WP4) for callers without email access; until then confirmation goes by email.

## Step 2: change requests (product owner, 2026-11-09)

The product owner decided how a caller is identified: "denk dat nog een id moet zijn met een
afspraaknummer/code om dit te borgen. Dit zal de rest van de vragen ook oplossen." A change
request names the booking by its phone number **and** its appointment code together, never by
the phone number alone. The notes below follow from that.

1. **The appointment code.** Every booking has an eight-digit code, derived as the management
   token is (ADR 0011): HMAC-SHA256 under `MANAGEMENT_TOKEN_KEY` of the booking's salt with its
   own purpose, reduced to eight digits. Nothing new is stored; a reschedule makes a new
   booking with a new salt and so a new code; after the purge the booking has no phone number
   and is no longer confirmed, so its code matches nothing.
   During a key rotation (ADR 0094) the code under the previous key is accepted as well.
   Digits, so a voice bot and a keypad can take it; shown as `1234 5678`.
2. **Where the participant sees it.** Only in organisations that switched change requests on
   (`agent_change_requests_enabled`): on the management page and in the confirmation, change
   and reminder messages ("Uw afspraakcode"). Elsewhere nothing changes.
3. **The request.** `POST /groups/{slug}/change-requests` with `phone`, `code`, `action`
   (`cancel` or `reschedule`) and, for a reschedule, `start` (a free time from the
   availability call). The answer is always `202` with a request id, `status: pending` and
   `expires_at`, whether or not the pair matches a booking that can still be changed. A wrong
   pair, an unknown number, a booking past its deadline and a match all get the same answer, so
   the API never tells whether a number or a code exists. Only what any caller can see anyway
   is refused before the match: a malformed phone number or code (`invalid_request`), a time
   that is not free (`slot_unavailable`), an organisation that has not switched the feature on
   (`feature_disabled`).
4. **Which booking.** The pair picks one booking: a number with several bookings is no longer
   ambiguous. The booking must be confirmed (not pending, suspended or awaiting a new time),
   in the URL's group, and still changeable as the participant's own link would allow (ADR
   0069: cancel until the start, move until the change deadline).
5. **Confirmation by the participant.** On a match the participant gets a message (email, and
   an SMS where they agreed to SMS) that names the requested change and links to
   `/b/m/{ref}/change`: the change and one button. A GET changes nothing (mail scanners); the
   POST applies it through the same paths as the participant's own cancel and reschedule. A
   newer request for the same booking replaces the open one (and drops its message if that has
   not gone out); the replaced request still answers `pending` until its own expiry, as an
   unmatched one does. Switching the organisation off stops open requests too. Nothing is held: a time taken in
   the meantime sends the participant back to the booking page, which says so.
6. **Expiry.** A request is open for `agent_change_request_minutes` after it was made (the
   same for a match and a non-match, so `expires_at` reveals nothing), and only while the
   booking's start is unchanged. After that the status is `expired` and the link shows the
   booking.
7. **Status.** `GET /change-requests/{id}` answers `pending`, `confirmed` or `expired`, only to
   the client that made the request (another client gets `not_found`). An unmatched request
   stays `pending` until it expires.
8. **Scope.** `bookings:request_change`, group level (ADR 0014 `API_SCOPES`); the client's
   group narrowing applies.
9. **Limits and audit.** `POST /change-requests` has its own per-client limit
   (`agent_change_requests_per_hour`) next to the general one. Every request is an audit event
   with the client id, the action and whether it matched (for the superuser, never in the
   answer); a confirmation is an event at the booking. No phone number, code or name is
   stored with the request, logged or audited.
10. **Data.** A table `change_requests` holds the public id, client, group, the requested start
    (none for a cancellation), the matched booking id (or none), the booking's start when asked,
    the times made, expiring, confirmed and replaced. No personal data; it goes with its group
    or booking.

### Step 2b: the three follow-ups (product owner, 2026-11-09)

The product owner asked for the follow-ups of step 2 as their own step, so the step is finished,
not half built.

11. **The client must still be allowed to ask.** When the participant opens or confirms a request
    and before its message goes out, the superuser's API switch must still be on, the client
    must still exist and be enabled, and the matrix must still grant it
    `bookings:request_change` on the group (`decide_client`: scope, organisation, narrowed
    groups). Otherwise there is nothing to confirm, and the message is not sent.
12. **A quiet period per booking.** After a request reached a booking, a new matching request for
    it within `agent_change_request_cooldown_minutes` sends nothing and replaces nothing: it is
    recorded as matching no booking (audit reason `cooldown`) and the API answers exactly as for
    any other request. Someone who knows a participant's number and code can send them at most
    one message per quiet period.
13. **Retention.** A change request, matched or not, is deleted
    `agent_change_request_retention_days` after it expired (hourly, with the worker's other
    housekeeping); its status is then `change_request_not_found`. The audit events stay under
    their own retention.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `agent_change_requests_enabled` | D organisation | off | on/off | organisation settings | Change requests for the organisation's groups, and the appointment code in its messages and pages. Switching it off is no loss of protection: the API refuses requests. |
| `agent_change_request_minutes` | D organisation | 60 | 10–1440 | organisation settings | How long the participant has to confirm a change request. |
| `agent_change_requests_per_hour` | C platform | 20 | 1–120 | `settings.platform.manage` | Change requests per API client per hour, next to `rate_limit_agent_calls`. |
| `agent_change_request_cooldown_minutes` | D organisation | 10 | 1–1440 | organisation settings | Step 2b: after a request reached a booking, minutes in which a new one for it sends nothing. A protection, so it cannot be switched off. |
| `agent_change_request_retention_days` | C platform | 30 | 1–365 | `settings.platform.manage` | Step 2b: days after expiry that a change request is kept. |

**Fixed on purpose:** the code has eight digits (a crypto parameter: 10^8 codes per phone
number, behind the per-client limit and the audit trail).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Clients, tokens, `/me`, groups and availability; the superuser's switch, client admin and audit | #18, #67, #97 | merged |
| 2 | Change requests by phone number and appointment code: scope `bookings:request_change`, `POST /groups/{slug}/change-requests`, `GET /change-requests/{id}`, table `change_requests` (migration 0105), the code on pages and messages, the participant's confirm page `/b/m/{ref}/change`, three settings | #688 | merged |
| 2b | The client must still be allowed to ask at confirmation and before the message; a quiet period per booking (`agent_change_request_cooldown_minutes`); deletion after `agent_change_request_retention_days` | #690 | merged |

## Consequences

- One more secret type to rotate (`docs/runbook.md`) and one more flow in
  `docs/threat-model.md` and `docs/dataflow.md`.
- The admin portal gets an "API clients" section; the matrix test covers its routes.
- `docs/agent-api.md` describes connecting a WxCC AI Agent (token, calls, errors).
- WxCC AI Agent's support for calling external HTTP actions with client credentials and the
  licence (O6) still have to be confirmed with the contact center.
