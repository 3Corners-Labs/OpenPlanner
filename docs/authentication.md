# Authentication

Every way a caller proves who it is, in one place: how strong the proof is, how it is
withdrawn, and what lies with the identity provider (IdP) instead of with OpenPlanner. The
browser side (cookies, CSRF, redirects, headers) is in the section *Browser model* of
`docs/threat-model.md`; rights after authentication are in `docs/authorization.md`.

There are three paths, and only the first involves a person who logs in:

| Path | Who | Proof | Strength | Withdrawn by | Lies with the IdP |
|---|---|---|---|---|---|
| Admin portal | Staff | OIDC code flow with PKCE, then a signed session cookie | Whatever the IdP enforces (password, MFA); we check the token, not the factor | Logout, 8 h end, IdP switch-off (see below) | Passwords, MFA, `acr`, account lifecycle, roles |
| Machine API | A contact-center bot | OAuth 2.0 `client_credentials`, then a 10-minute bearer token | One secret per client, stored as a hash | Disable or delete the client | Nothing |
| Participant link | A participant | Possession of the link from the email | Bearer link, 32 random bytes | The booking ends or is purged | Nothing |

Participants never log in and have no account (CLAUDE.md). Nobody authenticates to the
widget endpoints; they are public and limited by rate limits and an origin allow-list
(ADR 0035).

## 1. Admin portal (ADR 0013, 0083, 0097, 0098)

**Flow.** `GET /admin/login` starts an authorisation request at the IdP (PingFederate in
production, Keycloak in development; a registered provider per organisation, ADR 0083):
`state`, `nonce` and a PKCE S256 verifier are generated and kept in the signed cookie.
`GET /admin/callback` checks `state` in constant time, exchanges the code, verifies the ID
token and starts the session. Only `httpx` and `PyJWT` are used; no login bypass and no role
switcher exist (CLAUDE.md).

**What is checked in the ID token.** Signature against the provider's JWKS with an allow-list
of asymmetric algorithms only (`alg: none` and HS256 are refused), `iss`, `aud`, `azp`, `exp`,
`iat` and `nonce`. A `kid` that is unknown triggers one refetch of the key set, then refusal.

**Issuer pinned (ADR 0097).** The issuer a token must carry is fixed before the first request
and compared exactly, with no trailing-slash tolerance: for the platform provider
`OIDC_ISSUER` (class B; empty means derived from `OIDC_DISCOVERY_URL`), for an organisation
provider the issuer stored at registration. A discovery document or token that names another
issuer refuses the login (`oidc.issuer_mismatch`, no values logged). The provider's
document therefore cannot move the issuer after the fact.

**Session (ADR 0098, built).** The session is a row on the server (`admin_sessions`, the reference
stored only as its SHA-256). The signed cookie `__Host-op_admin` (`Path=/`, `Secure`, no `Domain`) when
`PUBLIC_BASE_URL` is https, `op_admin` (`Path=/admin`) over http, `HttpOnly`, `SameSite=Lax`, holds
only a random reference next to the login in progress and preferences; claims, roles and entitlements
live on the server. Every request looks the row up and accepts it while it is not ended, younger than
`admin_session_hours` (8 h) and active within `admin_idle_minutes` (60 min). Every login, a re-login
included, creates a new session and ends the one the browser brought; at most
`admin_max_sessions_per_person` (5) run at once. Changes need a login younger than
`admin_reauth_minutes` (60 min, ADR 0015).

**How strong.** OpenPlanner does not run the authentication factor; the strength is that of the
IdP's own policy. It can require proof of a second factor: with the second-factor requirement on
(ADR 0100, `ADMIN_MFA_REQUIRED`, or the policy of a registered provider) a login whose ID token shows
no `amr` or `acr` from the configured lists is refused (`mfa_required`). For PingFederate the value to
require is for the IdP team to name; the accepted values stay configurable. What OpenPlanner adds: a
fresh login for changes (`admin_reauth_minutes`), `fresh=1` logins that force a new IdP login
(`prompt=login`, `max_age=0`) for a mapping dry run, and rights derived only from claims through the matrix.

**How it is withdrawn.**

| Case | What happens |
|---|---|
| The admin logs out | `POST /admin/logout` ends the session on the server first, then clears the cookie and ends the IdP session (RP-initiated logout). A copy of the cookie is dead at once. The page the logout ends at also sends `Clear-Site-Data: "cookies", "storage"` (see below). |
| A person leaves or is switched off at the IdP | Their sessions run until idle (60 min) or their 8 h end unless an administrator ends them (*End all sessions* under `staff.manage`, or suspending the person, ADR 0046 step 2); the next login fails. The person can end their own other sessions (*My sessions*). `sessions end-all` on the host ends everyone's. |
| Role or membership changes at the IdP | Takes effect at the next login; the difference is audited (`staff.membership_changed`). |
| Idle | `admin_idle_minutes` (class C, default 60, 15-120). |
| IdP outage | Admins cannot log in. There is no break-glass login; the host CLI offers closure and `oidc-check` only. |

**Clear-Site-Data at logout (ASVS V14.3.1).** `GET /admin/logged-out`, the page every logout ends at (after
the IdP's own logout, or at once when the IdP offers no logout address), sends
`Clear-Site-Data: "cookies", "storage"` (`LOGOUT_CLEAR_SITE_DATA` in `app/admin/session.py`, tests in
`tests/admin/test_admin_sessions.py`). The server side has ended the session before that, so the header
is a second layer: the browser also drops cookies and local data of the site. Choices:

- **Where.** Not on the `POST /admin/logout` response: that one is followed by the IdP's logout, and
  `"cookies"` also clears the cookies of an IdP on the same host or registrable domain (the end-to-end
  suite stalled on exactly that with Keycloak on the same host), so the IdP could not finish and return.
  The final page comes after all of that. The header is sent only when the signed session cookie carries a
  mark set by this browser's own logout, once; a link from another site to `/admin/logged-out` clears nothing.
  If the person abandons the IdP step, the header does not come; the server session has ended anyway.

- `"cache"` is left out. Every HTML response is `Cache-Control: no-store`, so the session leaves nothing in
  the HTTP cache; `"cache"` would also empty the caches of participants' pages and static files. Firefox
  does not support it either.
- Browsers apply the header only over HTTPS (a secure context) and `"cookies"` covers the site's origin
  and its subdomains. Because of the `__Host-` prefix our cookies have no `Domain`, so nothing is shared
  with other sites. A side effect: an administrator who is also a participant in the same browser loses
  the participant cookie (ADR 0093) too and opens the booking from the email link again. The participant
  side sends no header of its own: ADR 0093 does not ask for one.
- It is a fixed protocol value, not a setting.

**What lies with the IdP.** Password policy and storage, MFA and its enrolment, account
lockout and recovery, the lifetime of the IdP's own SSO session, the mapping of people to
the role and scope claims (ADR 0049), and the correctness of the keys it publishes. We
verify; we do not administer.

## 2. Machine API for bots (ADR 0023, 0084)

**Flow.** `POST /api/agent/v1/oauth/token` with `grant_type=client_credentials` and the
client id and secret, preferably as HTTP Basic (form fields also work, never both). It is the
only grant the endpoint knows; any other value is `unsupported_grant_type`. The answer is a
bearer token that lives 10 minutes (`expires_in: 600`), with `Cache-Control: no-store`. A
client holds at most five live tokens; older ones are revoked on issue. Calls carry
`Authorization: Bearer`.

**How strong.** The secret is generated by the server, shown once and stored only as a hash;
a lost secret means a new one (rotate). Failed token requests are rate limited per address
(`agent_tokens`) A token carries scopes (today
`availability:read`) and reaches only the groups granted to its client. There is no user
context and no session, and no cookie is involved, so none of the browser protections
apply; the token is not usable from a page because the endpoints send no CORS headers.

**How it is withdrawn.** An admin with `api_clients.manage` disables a client (its tokens stop
working at the next call) or deletes it (ADR 0084; the audit events stay). Rotating the secret
revokes the existing tokens. Changing what a client may reach also revokes its tokens, and a token expires by itself after 10 minutes at the latest.

**What lies with the IdP.** Nothing. The client credentials belong to OpenPlanner and are
unrelated to the IdP; the bot's own platform (for example the Webex AI Agent connector)
stores the secret on its side.

## 3. Participant links (ADR 0011, 0056, 0069, 0093)

**Flow.** The booking confirmation email carries `/b/{token}`. The token is derived as
HMAC-SHA256 of a 32-byte per-booking salt under `MANAGEMENT_TOKEN_KEY`; the database keeps
the salt and `sha256(token)`, never the token, and comparison is constant time. The link is a
bearer credential: whoever holds it can confirm, cancel, move, test the call and join that
one booking. Confirming by email (ADR 0056) is a second step on the link, a button that
POSTs, not a link that acts. There is no account or password. Since ADR 0093 the link is
exchanged for a cookie (below).

**ADR 0093 (built).** `GET /b/{token}` is exchanged once for the cookie
`__Host-op_link` (opaque random value, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no
`Domain`; over plain http in development `op_link` with `Path=/b`; the database keeps the
SHA-256 of the value) and redirects with `303` to `/b/m/{public_ref}`,
so the token stays out of browser history and the address bar. The session lives
`participant_link_session_hours` (class C, 20-168, default 24) after its last use and holds at
most 20 bookings; the rule for the cookie's name and attributes is shared with, and separate
from, the admin cookie (`app/web/cookies.py`). The `public_ref` names a
booking and authorises nothing; the cookie's session must hold the booking. The cookie proves
possession of the link, not an identity. Old `/b/{token}/…` links keep working through the
exchange. Every participant page (booking, confirm, cancel, move, trial, join and the waiting room of a
direct call, including its poll) is on `/b/m/{ref}`; a direct call sets the cookie itself when
it starts. Only the first request from an email carries the token. No access log, route
templates in traces, `Referrer-Policy: same-origin` and `no-store` pages stay as defence in depth.

**How strong.** 256 bits of entropy behind an HMAC; guessing is not feasible and is also
rate limited (unknown links draw on one bucket per address, `unknown_links`) and recorded as
`token.rejected`. The weak point of any bearer link is the mailbox: someone with access to the
email can act as the participant. We accept that for appointments of this kind and keep the
reach small: a link opens one booking, shows no more than the participant gave, and offers
cancel and move only until the start with a minimum lead time (ADR 0069).

**How it is withdrawn.** Cancelling ends the booking and with it the useful life of the link.
Twenty-four hours after the slot ends or the booking is cancelled `purge_pii` removes the
personal data, after which the page shows nothing about the booking. Moving a booking creates a new
booking with its own token and cancels the old one, so the old link stops working (ADR 0011). A participant cannot revoke a link; the
organisation can cancel the booking. After ADR 0093 the participant cookie also lapses after
`participant_link_session_hours`, and the purge removes the booking from every session.

**What lies with the IdP.** Nothing. The video side is separate: the participant joins Webex
as a guest through the join link of the meeting (ADR 0075); Webex does not authenticate the
participant either, and the join link is itself a bearer URL held on the booking and purged
with it.

## What this document does not cover

Webhooks from Webex Connect and WxCC are authenticated by a signature or token and a
300-second window (CLAUDE.md, `docs/webhooks.md`), not by any of the paths above. Outbound
calls to Webex use OAuth tokens of a service app (ADR 0007).
