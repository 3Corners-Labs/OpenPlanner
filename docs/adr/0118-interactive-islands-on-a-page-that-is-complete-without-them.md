# 0118. Interactive islands on a page that is complete without them

Status: proposed (2026-11-06), requested by the product owner. Partly built: step 1 only (see
*Build status*). Amends ADR 0016 §1–2
(admin forms without JavaScript) and the admin-UI and Playwright lines of `CLAUDE.md`; extends
ADR 0072 §6 (`live-region.js`), ADR 0048 and ADR 0061 §5 (`app/web/charts.py`). It stands on its own
and can merge before or after ADR 0117 (reporting), which is its first intended user: see also
ADR 0117 points 10, 19 and 20. The booking widget is out of scope (point 2). Amended by ADR 0123:
its points on the kinds of page (2) and on the script switch (5, `scripts=True` the only switch,
no third variant) now allow the call page as a third kind of page.

## Context

ADR 0016 chose server-rendered forms that work without JavaScript, and later ADRs (0010, 0048,
0061, 0072) allowed small scripts for live parts, on one page at a time, under `script-src 'self'`.
That worked while the admin was forms and lists. The product owner now wants reports, dashboards
and a live wallboard that feel modern and interactive (ADR 0117), and asked what can be relaxed
without drowning the project in dependencies.

Two things were mixed up in the old wording.

1. **WCAG 2.2 does not forbid scripts, frameworks or modern charts.** It forbids patterns: a chart
   that is only a canvas (1.1.1, 4.1.2), a tooltip that appears only on hover (1.4.13, 2.1.1), zoom
   or selection that needs dragging with no alternative (2.5.7), colour as the only signal (1.4.1,
   1.4.11), auto-updating content with no way to pause it (2.2.2), changes with no announcement or a
   lost focus (4.1.3, 2.4.3), a chart that does not reflow at 320 px (1.4.10), targets under 24 px
   (2.5.8) and time limits with no warning (2.2.1). Each of these can be avoided in an interactive
   design, one pattern at a time.
2. **The real costs of scripts are ours:** a looser content security policy, supply-chain risk of
   front-end packages, a build chain, and one writer on SQLite behind a growing number of polling
   clients. Those are the things to bound, and a rule that says "no JavaScript" bounds them
   bluntly while keeping out things that are cheap and safe.

The browser already does most of what a modern interface needs (custom elements, ES modules,
`<dialog>`, the popover attribute, container queries, `:has()`, `light-dark()`, `Intl`,
cross-document view transitions). Using those costs no dependency.

## Decision

1. **The rule.** Every task a person must be able to do (every form, every action, every number
   they read) works without JavaScript. A page may add *islands* on top: behaviour that makes it
   faster or richer, never the only way to read or do something. The page without scripts is
   complete and correct; this replaces "no JavaScript" in ADR 0016 §1–2 and in `CLAUDE.md`.
2. **What an island is.** An own script under `app/web/static/`, served from the same origin, with no
   bundler, no framework and no build step, that only enhances content the server already rendered
   (the SVG, the table, the link). If it fails to load, the content stays. Existing islands are the
   classic scripts `live-region.js`, `update-progress.js`, `calendar.js` and `alert-popups.js`,
   which bind to `data-` markup; they need no rewrite, only their entries of point 12. A **new**
   island is an ES module that defines a custom element (`op-…`) wrapping the server-rendered
   content. The booking widget (`widget/`, Shadow DOM, built with esbuild, running on host sites) is
   not an island and keeps its own rules in `CLAUDE.md`. Charts: the SVG and the table text come from
   the one server-side home, `app/web/charts.py` (ADR 0061 §5); an island enhances that output and is
   never a second chart renderer.
3. **Browser features that need no decision.** Custom elements, ES modules, `<dialog>`, the popover
   attribute, container queries, `:has()`, `light-dark()`, `Intl` formatting, cross-document view
   transitions, CSS animation, `fetch`, `ResizeObserver` and `IntersectionObserver`. A feature is
   used only when it is *widely available* in the Baseline of the browsers the project supports, or
   when the page is correct without it; the check is recorded in `docs/islands.md` per element.
   Motion honours `prefers-reduced-motion`, colour honours `forced-colors` and `prefers-contrast`.
4. **Updating a page: one mechanism.** `live-region.js` fetches server-rendered fragments on an
   interval, as ADR 0072 §6 defined. It already refuses redirects, retries after a failure and enforces a minimum interval of 5 s;
   these rules are added, not duplicated: focus is never lost by an update (it moves only when the
   element that had it is replaced, and then to the heading the region names, as today); a fragment
   URL comes only from a server-rendered `data-` attribute and is a same-origin relative path; the
   fragment is parsed as DOM from the server's response (escaped by Jinja) and never built by
   string concatenation; a fragment or island page that carries scoped or suppressed data is sent
   with `Cache-Control: no-store`; a change is announced in a polite live region only when it matters
   (a threshold crossed, a result arrived), not on every poll; `aria-busy` is set while a fragment
   loads; a fragment that fails three times in a row marks the page as stale in text; and an
   update that runs without being asked for more than five seconds has a pause control (WCAG
   2.2.2) unless the content is essential, in which case the page says so. WebSockets,
   server-sent events and HTMX are not used. Server-sent events may be reconsidered by a later ADR
   if measured polling load on the web container makes it necessary.
5. **Content security policy.** `SCRIPT_CSP` (`script-src 'self'`, with `connect-src 'self'` for the
   fragments) stays the one policy for a page with islands and `scripts=True` stays the only switch;
   there is no third variant. A page without islands keeps `HTML_CSP`, which allows no script source
   (`default-src 'none'`). Because `'self'` trusts every same-origin file, uploaded files (logos,
   fonts, images) are served with a fixed non-script content type and `X-Content-Type-Options:
   nosniff`, never from a path a script could be loaded from, and a test asserts it. Never `unsafe-inline`, never `unsafe-eval`, never an external origin for scripts, styles, fonts,
   images or data. A library that needs one of those is out.
6. **Dependencies: none by default.** Islands are our own code. A third-party front-end library
   needs its own ADR and an entry in `docs/dependencies.md`, and only if it meets all of these:
   a licence on the allowed list (MIT, BSD, Apache-2.0, PSF; anything else by ADR, as for
   `dnspython`); no runtime dependencies of its own; maintained and the de-facto standard for what
   it does; output as DOM or SVG (canvas only with a full text alternative on the page); no
   `eval`; vendored as one file under `app/web/static/vendor/` with its checksum in a manifest that
   Renovate tracks, so `make pins` covers it; and it fits the budget below. The vendored file gets a component entry in the CycloneDX SBOM, the
   licence check and the repo's JavaScript rules, because `pip-audit` and `grype` on Python packages
   do not see it. No front-end package is installed by a build at deploy time.
7. **Budget.** Every script under `app/web/static/` stays under 30 KB gzipped and all of them together
   under 100 KB, checked by a pytest under `tests/web/` (so `make fast` runs it; no new Makefile
   target). When a page needs a list of its scripts, that list is declared where `scripts=True` is
   set and the budget becomes per page. These are fixed gates, not settings (they protect the CSP and the
   bandwidth of an unattended screen, not a tuning knob).
8. **The browser gets only what the person may see.** Scope filtering and any suppression of small
   numbers (for reports, ADR 0117 point 8) happen on the server. A suppressed or out-of-scope value is never in the
   HTML, a `data-` attribute, a JSON fragment or a script; an island does not receive raw data it
   would then hide. Report data is not kept in `localStorage`, IndexedDB, a service worker cache or
   a cookie. An island does not build a file from data that is not on the screen; an export is a
   server response, scoped and audited.
9. **Accessibility rules for every island** (WCAG 2.2 AA, in addition to the rules in
   `CLAUDE.md`). Each is part of the element's entry in `docs/islands.md`:
   - **Text alternative:** a graphic has its numbers in a table on the page (1.1.1); an interactive
     graphic has accessible names and roles on its parts (4.1.2).
   - **Keyboard:** everything a pointer does has a key path with a visible focus; arrow keys move
     within a composite widget with a roving tabindex; no keyboard trap; Escape closes a layer and
     returns the focus to its trigger (2.1.1, 2.1.2, 2.4.3, 2.4.7).
   - **Pointer:** nothing needs hover (a tooltip appears on focus too, stays while hovered and can
     be dismissed, 1.4.13); nothing needs dragging (zoom or select ranges also by buttons or a
     form, 2.5.7); targets are at least 24 × 24 px (2.5.8).
   - **Colour and contrast:** series differ by label, pattern or shape as well as by colour
     (1.4.1); graphics have 3:1 against their background (1.4.11); text 4.5:1, in light, dark,
     `forced-colors` and `prefers-contrast`.
   - **Status:** results and errors are announced without moving focus (4.1.3); auto-updating
     content can be paused (2.2.2); a limit in time is stated before it ends (2.2.1).
   - **Reflow:** the island works at 320 px and at 400 % zoom with no two-dimensional scrolling,
     except a table or a diagram in its own scroll container (1.4.10); text spacing overrides do
     not break it (1.4.12).
   - **Text in the DOM** comes from the i18n catalogue or from codes, is set with `textContent` and
     never with `innerHTML`; a label from outside the page (a group name, a subject) is never
     trusted. A semgrep rule in `.semgrep/` forbids `innerHTML`, `outerHTML`, `insertAdjacentHTML`,
     `document.write`, `eval` and `new Function` under `app/web/static/`, and the threat model gets
     an abuse case with a test (a group name containing markup is shown as text).
10. **Test gates**, added to the Playwright gate of `CLAUDE.md` for every page in `e2e/pages.json`
    marked `island` (a field on entries of both lists there, read through `e2e/pages.ts`), in every
    locale, as shared checks in `e2e/helpers.ts` (scripts off is one context option,
    `javaScriptEnabled: false`) and not as a copy per page:
    - the page with scripts disabled renders every number and every control and has zero axe
      violations;
    - with scripts enabled: zero axe violations, and a keyboard-only run of every interaction in
      the element's keyboard map;
    - 320 px and 400 % zoom: no horizontal page scroll;
    - `forced-colors`, `prefers-contrast` and `prefers-reduced-motion` emulation: content and
      controls stay readable and no animation runs;
    - no console error from the content security policy;
    - the size budget of point 7;
    - before a minor release, a manual pass with NVDA and Firefox and with VoiceOver and Safari
      for each *new kind* of island, recorded in `docs/a11y-audit.md` (axe finds only part of the
      problems; this is the check for the rest).
11. **Not allowed.** A single-page-application framework (React, Vue, Svelte or similar) or a
    front-end build chain with a package tree; HTMX or a second update mechanism; anything loaded at
    run time from a CDN; analytics, tracking or advertising scripts; embedding a BI product or
    another origin's page; a library that needs `eval` or an external origin; a canvas or WebGL
    graphic without a text alternative; sending hidden or unsuppressed data to the browser (point
    8). A need that none of the rules covers goes to a new ADR; it is never solved by loosening the
    content security policy.
12. **Documentation.** `docs/islands.md` lists every island: element name, file, what the server
    renders inside it, attributes, keyboard map, roles and live regions, features used and their
    support, what happens without the script, and its size. A test fails when a script under
    `app/web/static/` (recursively, so `vendor/` too; the existing `tests/web/test_static.py` is
    extended, not a third script-scanning test) is missing from it.

## Settings

None. The content security policy, the size budgets and the lists above are fixed on purpose:
they are security and accessibility rules, not tuning values, and a setting would let a platform
installation loosen what the page is allowed to execute (CLAUDE.md: switching a protection off is
never C or D). The refresh interval of live fragments stays the existing `QUEUE_REFRESH_SECONDS`
(ADR 0072) and ADR 0117's `reporting_wallboard_refresh_seconds`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; the Status line of ADR 0016; the admin-UI and Playwright lines of `CLAUDE.md`; the row in `docs/adr-status.md` | #559 | merged |
| 1 | `docs/islands.md` with the four existing scripts; the test that every script is listed and the size pytest (extend `tests/web/test_static.py`); the semgrep rule on `innerHTML` and friends; the XSS abuse case; the upload content-type test; the CLAUDE.md detail moves there (already short since step 0) | #560 | merged |
| 2 | Playwright gates for pages marked `island`: scripts off, keyboard map, 320 px and zoom, forced colours, reduced motion, CSP console; `e2e/pages.json` flag | | planned |
| 3 | `live-region.js` rules of point 4 (stale marker, pause control, announce only on change) and their tests | | planned |
| 4 | The first new island (`op-chart` of ADR 0117 step 12) built to this ADR, with its entry and manual pass | | planned |

Each step is one PR and answers the eight review questions. Steps 1 to 3 add no endpoint, take no
outside input and cannot reach personal data (the pages under test are the existing ones with
seed data). Step 3 changes a shared script, so it gets the security review of the shared-script
rules (no `innerHTML`, no new origin).

## Consequences

- Reports and the wallboard can be interactive without a framework or a package tree, and each
  interaction has a path that works without a pointer, without colour and without scripts.
- `CLAUDE.md` and ADR 0016 stop saying "no JavaScript" and say what is actually protected: the
  page works without it, and what runs is ours, same-origin and bounded.
- The CI gain is real work: the Playwright gate grows by five checks per island page, and a
  manual screen-reader pass is needed for each new kind of island.
- A front-end library is possible but costly by design (an ADR, vendoring, a checksum, a budget),
  which keeps the dependency list short without forbidding it.
- Server-sent events, a canvas chart library and a stricter reading of "widely available" are
  deferred to later ADRs with measurements; this one does not decide them.
