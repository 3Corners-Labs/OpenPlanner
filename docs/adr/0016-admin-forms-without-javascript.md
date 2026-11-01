# 0016. Admin forms without JavaScript; HTMX only where it adds something

Status: accepted (2026-10-23)
§1–2 amended by ADR 0010, 0048, 0061 and 0072: a page that needs live parts loads a small own
script (`update-progress.js`, ADR 0048; the generic `live-region.js`, ADR 0072 §6, on the
queue, the claimed booking and the agenda day view) with `SCRIPT_CSP` (`script-src 'self'`) on
that page only; HTMX is not used.

## Context

CLAUDE.md names Jinja2 + HTMX for the admin UI. The pages in WP7a and WP7b are forms and
overviews: create a group, change settings, add or remove opening hours and exceptions. HTMX
needs `script-src 'self'` in the Content Security Policy, a vendored script and a dependency
review. Today the CSP allows no scripts at all.

## Decision

1. Admin pages are server-rendered HTML forms with post/redirect/get and a confirmation
   message. No JavaScript, so the CSP stays without `script-src`.
2. HTMX is introduced only when a screen clearly gains from partial updates (for example a
   live meeting status, ADR 0010). It then comes with its own review and a CSP change in the
   same PR.
3. The admin portal has one fixed, modern look (`app/web/static/admin.css`) for every
   organisation. Organisation branding (O5) applies to the citizen pages.

## Consequences

- Every form works with keyboard, screen reader and without scripts. Errors are linked to their
  fields and the input is kept.
- A change reloads the page. For forms of this size that is fast enough.
- CLAUDE.md's stack line now reads "Jinja2, server-rendered; HTMX only per ADR 0016".
