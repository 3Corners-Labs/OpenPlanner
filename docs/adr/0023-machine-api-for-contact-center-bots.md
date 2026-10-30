# 0023. Machine API for contact-center bots (WxCC AI Agent)

Status: accepted (2026-10-25). First step of open point O11. Partly built, checked on 2026-10-30; see `docs/adr-status.md` for what is built.

## Context

The Webex Contact Center AI Agent (chat and voice bots) should be able to help citizens with
their appointments. Webex CC does the conversation, speech recognition and text-to-speech; we
deliver only an API on the planning (HANDOVER §11, O11). The citizen identifies with the
phone number of the booking and confirms every change through a message to email or SMS
that leads to the existing management page.

This is the first entry point into OpenPlanner that is neither a citizen in a browser nor an
admin with an IdP login. It must not become a back door: it may never do more than a
citizen with the management link can, it must be switchable off at once, and every use
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
   (next step) creates a *change request*; only the citizen's confirmation through their own
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
| next | `POST /change-requests` (phone number + reschedule/cancel) | `bookings:request_change` |
| next | `GET /change-requests/{id}` (pending, confirmed, expired) | `bookings:request_change` |

The change-request flow (next step) answers the same way whether or not the phone number
matches a booking, so the API cannot be used to find out who has an appointment. It depends
on SMS (WP4) for callers without email access; until then confirmation goes by email.

## Consequences

- One more secret type to rotate (`docs/runbook.md`) and one more flow in
  `docs/threat-model.md` and `docs/dataflow.md`.
- The admin portal gets an "API clients" section; the matrix test covers its routes.
- `docs/agent-api.md` describes connecting a WxCC AI Agent (token, calls, errors).
- WxCC AI Agent's support for calling external HTTP actions with client credentials and the
  licence (O6) still have to be confirmed with the contact center.
