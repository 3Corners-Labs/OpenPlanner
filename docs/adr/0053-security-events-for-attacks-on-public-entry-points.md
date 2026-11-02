# 0053. Security events for attacks on public entry points

Status: accepted (2026-10-29), requested by the product owner. Extends ADR 0034 (security
events, AU-4) and ADR 0040 (event schema); the schema stays `openplanner.event/1`. Built, checked against the code on 2026-10-30 (status was *proposed*; `docs/adr-status.md`).
The schema is `openplanner.event/2` since ADR 0065.

## Context

ADR 0034 made two kinds of security event: a rejected webhook and a failing Webex token
refresh. Everything else an attacker does against the public surface leaves at most an
ordinary log line, or nothing:

| Attempt | Today |
|---|---|
| Rate limit hit (booking pages, admin login, machine API) | 429 page, no event |
| Honeypot field filled in (bot on the booking form) | 400 page, no event |
| Cross-site or wrong-origin POST (CSRF attempt) | `request.rejected` log line |
| Failed admin login (bad `state`, invalid ID token, nonce mismatch) | audit event, actor `anonymous`, no client |
| Unknown bearer token on the machine API (token guessing) | log line only, by design of ADR 0023 |
| Unknown management link `/b/{token}` (link guessing) | 404 page, no event |

The security team (SOC) cannot alert on what is not an event, and cannot tell one scanner
from many visitors because no event says *who* was calling. The threat model lists slot
exhaustion by bots and token guessing as abuse cases; OWASP ASVS 5.0 V16 (security logging)
and the BIO ask that such attempts are logged with enough detail to trace them.

The caller's IP address is personal data (AVG/GDPR). Storing it raw in `audit_events` and on
the log platform would put personal data of citizens in a place CLAUDE.md forbids it.

## Decision

1. **Six attempts become security events.** Each is a global event of category `security`
   with outcome `denied`, written through `SecurityLog` (ADR 0034) and shipped like every
   security event:

   | `event.action` | `reason` | Where |
   |---|---|---|
   | `request.rate_limited` | `too_many_requests` | HTML 429 handler; machine API 429 handler (detail `surface:web` or `surface:agent`); the unknown-link bound of point 7 (detail `surface:links`) |
   | `request.rejected` | `honeypot` | booking and direct-call forms |
   | `request.rejected` | `cross_site`, `origin_mismatch` | `require_same_origin` |
   | `admin.login` | the identity error code (`invalid_state`, `invalid_token`, `nonce_mismatch`, …) | `/admin/callback` |
   | `token.rejected` | `unknown_token` | machine API bearer check |
   | `token.rejected` | `unknown_management_link` | `/b/{token}` pages answering 404 |

   A failed admin login was an audit event; it becomes a security event with the same
   action, actor (`anonymous`) and reason, so the audit log keeps showing it to the
   superuser and the existing filters still find it.
2. **Who called: a pseudonym, never the address.** Every event of point 1, and from now on
   `webhook.rejected`, carries `client:<pseudonym>` in `detail`. The pseudonym is the first
   16 hex characters of HMAC-SHA256 over the caller's address, under a key derived with
   HKDF-SHA256 from `MANAGEMENT_TOKEN_KEY` (label `openplanner/client-pseudonym/v1`, in
   `app/keys.py`). IPv4 addresses are taken whole; IPv6 addresses by their /64 prefix, since
   one host owns a whole /64. Without a known address the pseudonym is `unknown`. The same
   address gives the same pseudonym on every event of this installation, so the SOC can
   correlate; nobody can turn it back into the address without the key.
3. **From address to pseudonym on the host.** `python -m app.cli client-pseudonym ADDRESS`
   prints the pseudonym of an address that the SOC already has (from a firewall or proxy
   log), so it can check whether that address is the one in the events. There is no way
   back from pseudonym to address.
4. **Deduplication per caller, bounded.** `SecurityLog` keeps its window per kind of event
   (`SECURITY_EVENT_WINDOW_MINUTES`); the kind now includes the client, so one scanner gives
   one event per window with `suppressed:<n>`, and a second scanner gets its own. At most
   `SECURITY_EVENT_MAX_SOURCES` windows are kept per process; when that is reached, expired
   windows are dropped first, and if it is still full the event counts under client `many`.
   A distributed attack therefore costs a bounded amount of memory and rows.
5. **One helper on the HTTP side.** `app/web/security_events.py` turns a request into the
   pseudonym and writes the event; a failure there is logged and never changes the answer to
   the caller. Routes and handlers do not call `SecurityLog` themselves.
6. **No request content.** Detail holds machine keys (`surface:web`, `client:…`,
   `suppressed:n`). Never a path, header, form value, token or address.

7. **Unknown management links are bounded per address (added 2026-10-30).** slowapi buckets
   per request path, so every `/b/{token}/...` path had a counter of its own and any number of
   different guessed tokens from one address went unlimited (guessing itself stays infeasible,
   ADR 0011; the cost was unbounded lookups and one limiter key per path). Setting
   `key_style="endpoint"` would instead make all citizens behind one address share the status
   poll and booking budgets, so it is not used. Instead the 404 handler for
   `booking_not_found` under `/b/` counts the answer in one bucket per client address, the same
   for every `/b/{token}` route (`count_unknown_link` in `app/web/protection.py`, on the
   slowapi limiter, so `RATE_LIMIT_ENABLED` is its off switch). Within the bound the answer is
   the 404 and the `token.rejected` event above; over it the caller gets the rate-limit page
   (429, `Retry-After`) and one `request.rate_limited` event with detail `surface:links` per
   caller and window (point 4). Requests with a valid link are never counted. The lookup of a
   guess still runs; the bound limits what an address learns and writes, not the cost of a
   single lookup, which stays one indexed hash comparison.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `SECURITY_EVENT_MAX_SOURCES` | C platform | 1000 | 10–100000 | `settings.platform.manage` (settings registry, ADR 0042) | windows kept per process (point 4) |
| `RATE_LIMIT_UNKNOWN_LINKS` | C platform | 20/hour | 5–100 per hour | `settings.platform.manage` | unknown management links per address (point 7); `RATE_LIMIT_ENABLED` stays the off switch |

`SECURITY_EVENT_WINDOW_MINUTES` (ADR 0034) now also applies to the new events.

**Fixed on purpose:** which attempts are security events (switching one off would switch off
a protection); the pseudonym's construction (HMAC-SHA256, 16 hex characters, IPv6 /64, the
HKDF label) as crypto parameters; reason codes, as part of the event contract with the SOC.

## Consequences

- The SOC can alert on rate-limit floods, form bots, CSRF attempts, failed logins and token
  guessing, and correlate them per caller, without receiving personal data in clear.
- The pseudonym is still personal data (pseudonymised, not anonymised): `docs/dataflow.md`
  lists it as a field shipped to the log platform; retention there follows ADR 0034.
- Rotating `MANAGEMENT_TOKEN_KEY` changes every pseudonym (and every management link,
  ADR 0011); the runbook says so.
- A legitimate citizen with a purged or mistyped link produces `unknown_management_link`
  events; one per window per caller, so a SOC rule should alert on volume, not on one event.
- The machine API's credential refusals at the token endpoint stay audit events of ADR 0023
  (they name the client); they are not duplicated.
- No migration: the events fit the existing schema through `detail`.
- `docs/audit-events.md`, `docs/threat-model.md` (abuse cases: slot exhaustion, token
  guessing), `docs/dataflow.md` and `docs/runbook.md` are updated in the same PR.
