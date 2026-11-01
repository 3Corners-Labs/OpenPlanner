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

**Session.** Today (ADR 0013 §5) the session is the signed cookie `op_admin`: `HttpOnly`,
`SameSite=Lax`, `Path=/admin`, `Secure` when `ENVIRONMENT=prod`, absolute lifetime
`admin_session_hours` (8 h) enforced on the server from the login time in the cookie.
Changes need a login younger than `admin_reauth_minutes` (60 min, ADR 0015). ADR 0098
(proposed, not built) replaces this with a server-side register: the cookie holds
only a random reference, every request looks the row up, logout and a new login end the old
session, there is an idle limit and a limit on parallel sessions, and a person or an
administrator can end sessions. Until it is built, a cookie copied before logout stays valid
until its 8 h end; the mitigation is `HttpOnly`/`Secure` and rotating `SESSION_SECRET`, which
ends everyone's session. This document describes the target state of ADR 0098 where it says
*after ADR 0098*.

**How strong.** OpenPlanner does not see or enforce the authentication factor. The strength
is that of the IdP's own policy; for PingFederate the required `acr` is an open decision and
not yet checked by the platform (ASVS V6.8.4 is tracked outside this document). What
OpenPlanner adds: a fresh login for changes (`admin_reauth_minutes`), `fresh=1` logins that
force a new IdP login (`prompt=login`, `max_age=0`) for a mapping dry run, and rights derived only from claims through the matrix.

**How it is withdrawn.**

| Case | Today | After ADR 0098 |
|---|---|---|
| The admin logs out | `POST /admin/logout` clears the browser's cookie and ends the IdP session (RP-initiated logout). A copy of the cookie stays valid until its end. | The row is ended first; a copy is dead at once. |
| A person leaves or is switched off at the IdP | Their session runs until its 8 h end; the next login fails. A superuser can rotate `SESSION_SECRET` to end all sessions. | A person's sessions are ended from the staff record; `sessions end-all` on the host ends all of them. Back-channel logout stays a later option. |
| Role or membership changes at the IdP | Takes effect at the next login; the difference is audited (`staff.membership_changed`). | Same. |
| Idle | No limit before the 8 h end. | `admin_idle_minutes` (class C, default 60). |
| IdP outage | Admins cannot log in. There is no break-glass login; the host CLI offers closure and `oidc-check` only. | Same. |

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
POSTs, not a link that acts. There is no account, password or cookie in today's flow.

**ADR 0093 (accepted, not built).** `GET /b/{token}` is exchanged once for the cookie
`__Host-op_link` (opaque random value, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no
`Domain`; the database keeps its SHA-256) and redirects with `303` to `/b/m/{public_ref}`,
so the token stays out of browser history and the address bar. The `public_ref` names a
booking and authorises nothing; the cookie's session must hold the booking. The cookie proves
possession of the link, not an identity. Old `/b/{token}/…` links keep working through the
exchange. Until the build lands, the token is in every participant URL; what limits that is
in ADR 0093 (no access log, route templates in traces, `Referrer-Policy: same-origin`,
`no-store` pages).

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
