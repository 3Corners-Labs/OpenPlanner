# 0035. Embedding on a host website: Web Component, CORS allow-list, no iframes

Status: accepted (2026-10-28) by the product owner, with the choices in "Decisions of
2026-10-28" below. Builds on HANDOVER B4/B13 and ADR 0020 (citizen calendar and branding).

## Context

The default delivery is a standalone page per group (`/g/{slug}`) in the organisation's
branding, linked from the client's website. HANDOVER promises an optional embedded variant:
`<script src>` plus `<gov-booking group="...">` on the client's own page, styled by the host
through `--booking-*` tokens and `::part()`. `docs/widget.md` lists that as WP3c.

Today nothing allows embedding:

- Every response sends `frame-ancestors 'none'` and `X-Frame-Options: DENY`, so the pages
  cannot be framed.
- There is no CORS configuration and no JSON API for the booking flow; the citizen pages are
  server-rendered forms.
- Booking POSTs are protected by `require_same_origin` (Fetch Metadata), which by design
  rejects a request from the host site.

Three ways to embed were considered:

- **iframe of the existing pages.** Least code, but the frame needs height messaging through
  JavaScript, focus and screen-reader context break at the frame border, the host's tokens
  cannot reach inside, and opening `frame-ancestors` exposes the booking form to clickjacking.
- **Web Component calling a JSON API** (HANDOVER B13). Inherits the host's font and tokens,
  keeps all ARIA inside the shadow root, and needs CORS for an allow-list of origins.
- **Server-side include by the host.** Puts our HTML and our security in the host's hands.

## Decision

1. **Web Component, no iframes.** The embedded variant is `<gov-booking group="{slug}">`, one
   vanilla-JS custom element under `widget/`, bundled by esbuild in CI. Framing stays forbidden
   (`frame-ancestors 'none'`) on every page.
2. **A small public widget API.** `/api/v1/widget/{org}/{group}/` offers: availability for a
   month, the free times of a day, and creating a booking. It reuses the domain services of the
   standalone pages, returns error codes (no text) and is part of `/api/v1`, so it is in the
   OpenAPI spec, schemathesis and the breaking-change rule.
3. **Allowed origins per organisation, opt-in per group.** An organisation admin with
   `embedding.manage` keeps a list of host origins (`https://www.gemeente.nl`), validated as
   exact scheme + host + port, no wildcards, no paths. Changes are audited (ADR 0031) with
   counts, not the origins. A group appears in the widget only when its own setting
   **Embed on websites** (`group.edit`) is on; it is off for every group until switched on.
   - CORS on the widget API answers only those origins, never `*`, never with credentials.
   - The booking POST replaces `require_same_origin` with `require_allowed_origin`: the request
     must carry an `Origin` in the organisation's list. No cookies are used, so there is no
     session to ride.
4. **Same protections as the standalone page.** Rate limits per IP (10 bookings/hour,
   60 availability reads/minute), the honeypot field, the same validation and the same
   response: the widget shows the confirmation, and the management link arrives by email only.
   The management token is never returned to the host page.
5. **What the host must set.** The host's CSP needs `script-src` and `connect-src` for our
   origin. The script is versioned (`/widget/v{n}/gov-booking.js`) and published with its SRI
   hash, so hosts can pin it. `docs/widget.md` documents attributes, tokens, parts, CSP and the
   origin registration.
6. **Scope of the first version.** The widget offers the month calendar, the free times of a
   day, the contact form and the confirmation. During an emergency closure (ADR 0024/0037) it
   shows the closure notice and offers no booking. The direct call (ADR 0019) and the time
   filters stay on the standalone page; the widget links to `/g/{slug}` for them.
7. **Accessibility inside the shadow root.** Everything in CLAUDE.md's accessibility rules
   applies to the widget: ARIA and live regions inside the shadow root, roving tabindex grid,
   focus moves to the next step's heading. Every widget state gets a page in `e2e/pages.json`
   (a test host page), so axe, Lighthouse and the keyboard-only flow cover it in nl and en.

## Decisions of 2026-10-28

The product owner chose, when accepting this ADR:

- **Opt-in per group** rather than every group of the organisation: an internal or trial group
  cannot appear on the website by accident.
- **A new permission `embedding.manage`** (organisation level; organisation admin and
  superuser; not locked). The proposal named `integrations.manage`, but ADR 0039 locked that
  permission to the superuser because it touches secrets. Origins are not secrets, and the
  organisation decides where its groups appear.
- **The group switch sits in the group settings under `group.edit`**, next to "Start a call
  right away"; group admins do not hold it.
- **Origins are `https` only.** `localhost`, `127.0.0.1` and `[::1]` (with a port, over `http`
  or `https`) are accepted only when `ENVIRONMENT` is `dev` or `test`, for the end-to-end host
  page and local trials.
- **First version** as in point 6: closure notice yes, direct call and filters by link.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `EMBEDDING_MAX_ORIGINS` | C platform | 20 | 1–100 | `settings.platform.manage` (ADR 0042) | Origins per organisation. From the environment until the ADR 0042 registry exists. |
| `Group.embedding_enabled` | group setting | off | on/off | `group.edit` | Whether a group appears in the widget. |
| Allowed origins | organisation data | none | `EMBEDDING_MAX_ORIGINS` | `embedding.manage` | The list itself. |

The rate limits are the existing `RATE_LIMIT_BOOKINGS` and `RATE_LIMIT_READS`, shared with the
standalone pages.

**Fixed on purpose:** the origin rule (exact `https` origin, no wildcards, no paths, no
credentials over CORS) and the localhost exception tied to `ENVIRONMENT`: switching a
protection off is never a portal setting. `MAX_ORIGIN_LENGTH` (200) bounds the column and is
longer than any real origin.

## Consequences

- One booking flow, two shells: the standalone pages and the widget call the same services, so
  rules and texts cannot drift. The widget fetches its texts from the i18n module per locale.
- The organisation decides where its groups may be embedded; a copied script tag on another
  site does not work.
- The threat model gets a flow "embedded booking" with abuse cases: a foreign origin posting
  bookings (blocked by the allow-list and tested), slot exhaustion from a host page (same rate
  limits), and the host page reading confirmation data (the response holds no PII and no
  token).
- `docs/dataflow.md` notes that the host site sees which group a citizen opened and that the
  citizen's browser sends name and email directly to OpenPlanner, not through the host.
- No iframe support: a host that cannot add a script uses the standalone page link.
