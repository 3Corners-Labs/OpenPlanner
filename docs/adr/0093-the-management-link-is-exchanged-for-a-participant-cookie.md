# 0093. The management link is exchanged for a participant cookie

Status: accepted (2026-11-01), requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V14.2.1). Refines the use of the token of ADR 0011; the token itself, its
derivation and its storage are unchanged. Not built; see *Build status*.

## Context

The participant manages a booking through `/b/{token}` (ADR 0011): confirm, cancel, move, the
trial call, the waiting room of a direct call, join. The token is a bearer credential that
stays valid until the booking is over, and it sits in the URL of every one of these pages.
That is a deviation from ASVS V14.2.1 ("the URL and query string do not contain sensitive
information").

What limits the damage today:

- the access log is off, in uvicorn and in the bundled proxy;
- trace and audit lines carry the route template, never the path;
- `Referrer-Policy: same-origin`, so other sites do not receive it in `Referer`;
- pages are `no-store`.

What it does not limit: the token sits in the browser history, the address bar, screenshots,
browser sync, extensions and the URL logs of TLS-inspecting company proxies. The email
necessarily carries the link; that part cannot change.

A browser stores only the final URL of a redirect in its history. So if the first request
trades the token for a cookie and redirects, the token remains only in the email and in that
first request.

Participants never log in (CLAUDE.md), and this does not change that: the cookie proves
possession of the link, not an identity.

## Decision

1. **Every booking gets a public reference.** `bookings.public_ref` is 16 random bytes,
   base64url, unique. It does not authorise anything; it only names the booking in a URL. It
   is not PII and survives the purge, like the booking row.
2. **`GET /b/{token}` exchanges and redirects.** The token is checked as today (ADR 0011, same
   rate-limit budget, `token.rejected` on failure). On success the server:
   - creates or extends a participant link session;
   - sets the cookie `__Host-op_link` (`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no
     `Domain`), with an opaque random value of 32 bytes; the database keeps only its SHA-256;
   - answers `303` to `/b/m/{public_ref}`.
3. **All further pages use the reference and the cookie.** The confirm, cancel, move, test,
   wait and join routes move from `/b/{token}/…` to `/b/m/{public_ref}/…`. A request is
   allowed only when its cookie's session holds that booking. Without such a cookie the page
   says, without saying whether the booking exists, to open the link from the email again.
   That request draws on the same token-guessing budget.
4. **Flows that create a booking set the cookie directly.** A direct call, a booking made on
   the group page and a rebooking set the cookie in their own response and redirect to
   `/b/m/{public_ref}…`. The token never appears in a URL the participant sees there.
5. **One cookie, several bookings.** A participant link session holds at most 20 bookings
   (the oldest drops out). It lives `participant_link_session_hours` after its last use; every
   request that uses it extends it. That limit is at least 20 hours, so it falls under the
   20-hour exception of WCAG 2.2.1 and no form has a time limit in practice. Expired sessions
   are deleted by the worker's sweep. A booking's purge removes it from every session.
6. **Old links keep working.** `/b/{token}/…` subpaths in emails already sent redirect through
   the exchange to their `/b/m/…` counterpart. The email templates keep `/b/{token}`.
7. **Storage and naming.** The sessions live in their own table (`participant_link_sessions`:
   value hash, booking ids, expiry), behind the `Transaction` port. If the admin-session
   register that the ASVS session work proposes lands first with a store that fits, step 2
   may reuse it; the cookie stays separate from `op_admin`.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `participant_link_session_hours` | C platform | 24 | 20–168 | `settings.platform.manage` | idle lifetime of a participant link session; the lower bound keeps WCAG 2.2.1 (20-hour exception); the email link always works again |

**Fixed on purpose:** at most 20 bookings per cookie (an abuse bound, not a tuning value); the
cookie name, its `__Host-` prefix and attributes; 16 and 32 random bytes for reference and
value.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #309 | in PR |
| 1 | `public_ref` (migration), participant link sessions (table, port, adapter, sweep), the exchange on `GET /b/{token}` | | planned |
| 2 | Booking pages (confirm, cancel, move) on `/b/m/{ref}`; old subpaths redirect; e2e | | planned |
| 3 | Direct call, waiting room (poll URL in `live-region.js`), trial and join on `/b/m/{ref}`; maintenance route list; e2e | | planned |
| 4 | Threat model, data flow, `docs/widget.md` and the manual where they show `/b/{token}` | | planned |

## Consequences

- A cookie appears on the participant side. It is strictly necessary for the service the
  participant asked for, so it needs no consent; the privacy statement mentions it.
- Opening the email link on a second device works, since each device makes its own exchange.
  A page left open for more than a day asks to open the email link again.
- E2E tests and `e2e/pages.json` change paths; the build PRs carry `ci:full`.
- The audit's V14.2.1 is met for every page except the first request from the email, which
  the standard's intent allows for an out-of-band link; the threat model says so.
