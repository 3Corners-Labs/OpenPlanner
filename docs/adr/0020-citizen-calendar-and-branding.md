# 0020. Citizen calendar as progressive enhancement; branding through tokens

Status: accepted (2026-10-25)

## Context

The citizen booking page showed a week as a list of radio buttons per day. CLAUDE.md asks for
a calendar grid (`role="grid"`, arrow keys with a roving `tabindex`, focus to the next step's
heading) and HANDOVER B12–B14 for pages in the organisation's branding, with the same
`--booking-*` tokens the embeddable widget will read. Until now the citizen pages ran without
any script (CSP without `script-src`), and the admin portal stays that way (ADR 0016).

A grid role promises arrow-key navigation. Without a script it cannot keep that promise, so
the role may only be there while the script runs.

## Decision

1. **Month calendar plus the times of one day.** `GET /g/{slug}?month=&day=` shows a month
   table and the free times of the chosen day, grouped into morning, afternoon and evening,
   plus a "first available" choice. A time button opens the form; when moving a booking it opens
   a confirmation (from → to), so one click never moves an appointment.
2. **Progressive enhancement.** Without JavaScript the calendar is a table of links and every
   choice loads a page. `static/calendar.js` (vanilla, no build step, about 200 lines) turns
   the table into an ARIA grid with one tab stop. Arrow keys, Home/End and Page Up/Down move
   through it. Choosing a day fetches the same page and swaps in its times, announces them in a
   polite live region and moves focus to the times heading. All text comes from the server;
   any failure falls back to normal navigation.
3. **CSP for citizen pages:** `script-src 'self'; connect-src 'self'`; still no inline scripts
   or styles (`app.web.templates.SCRIPT_CSP`). Admin pages keep the CSP without scripts.
4. **Branding per organisation** (open point O5, minimal answer): a display name, one accent
   colour and one logo.
   - The domain refuses an accent below 4.5:1 against white, so white text on the accent and
     accent text on white are both readable.
   - Logos are PNG, JPEG or WebP, recognised from their bytes, at most 200 kB, stored in the
     database. SVG is refused because it can carry scripts.
   - The colour reaches the browser only as `/o/{slug}/theme.css` (one custom property,
     generated from the validated value); no inline styles.
   - Changing branding needs `branding.manage`, superuser only (HANDOVER §6), with a fresh
     login and an audit event.
5. **Tokens:** `site.css` uses only `--booking-*` custom properties with NL Design System
   defaults. The theme overrides `--booking-color-accent`; hover and tint colours are derived
   from it with `color-mix`. The embeddable widget (WP3c) reuses the same names
   (`docs/widget.md`).

## Consequences

- Keyboard and screen-reader users get the grid pattern; without JavaScript the page still
  works completely (tested in Playwright with JavaScript off).
- Citizen pages now run one first-party script; the CSP still blocks injected scripts.
- Choosing a day with JavaScript costs one page request (same rate limit as a page view).
- One logo and one colour per organisation answer O5 for now; if communications need more
  (header colour, fonts), that is new tokens in `theme.css`, not new rules.
- Organisation admins cannot change their branding; granting it to them is a one-line change
  in the matrix if HANDOVER §6 changes.
