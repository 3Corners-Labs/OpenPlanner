# Backlog: admin portal refresh

Work items that bring the admin portal design (Claude Design project *OpenPlanner*, `templates/admin-shell`, `templates/logo`, `templates/icons`) into the product. Decisions come from ADR 0047 and the proposed ADRs 0060–0063. Sizes: S ≈ up to 2 days, M ≈ up to a week, L ≈ more than a week. Each item is one PR with the usual gates (CLAUDE.md); "done" always includes tests, `nl`/`en` texts where users see something, the manual topic (ADR 0033) and the docs named in the item. Items that touch templates, navigation or authorisation carry the label `ci:full` (ADR 0058).

Suggested order: logo and icons (small, visible at once), shell to ADR 0047 (unblocks the rest), PII rule (a protection, before any new list), overview without script, then live updates, layout and branding.

## 0. Decisions

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| AP-0a | Product owner accepts ADR 0060 (logo) and 0063 (PII display); numbers checked against `origin/main` and open PRs | S | Status `accepted`; HANDOVER §11 updated | done: ADR 0060 and 0063 accepted |
| AP-0b | Product owner accepts ADR 0061 (live overview); 0062 (layout) stays `proposed` until ADR 0046 is built | S | Status recorded; dependency on 0046 noted in 0062 | done: ADR 0061 accepted; 0062 stays proposed |

## 1. Logo and favicon — ADR 0060

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| LG-1 | Brand files in `app/web/static/brand/` (`mark.svg`, `mark-mono.svg`, `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`); token `--op-brand-slot` light and dark in `admin.css` | S | SVGs without scripts or external references (test); `REUSE.toml` annotated; `reuse lint` green | built (#135) |
| LG-2 | Admin top bar shows the mark instead of the "OP" span in `layout.html` (`admin-brand__mark`); favicon linked from `base.html` | S | axe zero violations; product name stays as text; both themes screenshotted in the PR | built (#135) |
| LG-3 | Citizen footer "Appointments via OpenPlanner" uses the mark when the organisation keeps it on (ADR 0041) | S | Email plain-text part unchanged; logo has a text alternative in the HTML part | built (#135) |
| LG-4 | Optional motion: `static/brand-motion.js` for sign-in and booking confirmed, CSS-only loader | M | No motion under `prefers-reduced-motion`; static mark without script; CSP change limited to the pages that load it | open |

## 2. Icons

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| IC-1 | Add the missing icons to `_icons.html` (tasks, status, search, filter, export, import, close, menu, more, sign-out), same 24-unit grid and stroke 2 | S | Every navigation entry in `navigation.py` names an existing icon (test); icons stay `aria-hidden` | built (#137) |

## 3. Shell to ADR 0047

The register (`app/web/navigation.py`), the three sections, the `<details>` profile with roles and the theme choice already exist on `main`. What remains is what the design adds on top.

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| SH-1 | Collapsed icon rail on tablet and a bottom bar with *More* on phone, CSS only (`<details>` for *More*); entries still from the register | M | Keyboard-only pass; axe zero violations at 375, 768 and 1280 px; `docs/admin-map.md` unchanged | built (#147) |
| SH-2 | Top bar end: status, tasks and alerts links before help, language and profile, each with a count where it applies; profile gains *My rights* and alert preferences | M | No role switcher anywhere (test greps templates); counts rendered server-side; links carry `aria-label` with the count | open |
| SH-3 | Organisation › group filter in the top bar for overview and lists: GET form, only objects in the user's scope, links to the object page | S | Abuse case "filter value outside scope" with a test; never an edit form | open |

## 4. Personal data — ADR 0063

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| PI-1 | `app/domain/privacy.py` with the fixed field list and classification | S | 100% coverage; mutation score ≥ 80% | built (#143) |
| PI-2 | Marker-value test: every list, overview, export and alert template rendered with marker PII; none may appear | M | Test fails on a deliberate leak; runs in the fast lane | built (#143) |
| PI-3 | List search and filters never match PII fields | S | Abuse case "PII through search" with a test in `docs/threat-model.md` | built (#143) |
| PI-4 | Read-only card *Platform → Advanced settings → Data fields* (`settings.platform.view`) | S | Auditor sees it; nobody can change it; manual topic *Data fields and personal data* | built (#143) |

## 5. Overview with widgets — ADR 0061

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| OV-1 | Widget register `app/web/widgets.py` and fragment routes, each with one permission | M | Matrix sweep covers every widget route; widget without permission or `nl`/`en` title fails a test | built (#141) |
| OV-2 | Overview renders visible widgets without script; *Refresh* link; role defaults | M | Page works with JavaScript off; Lighthouse accessibility ≥ 95 | built (#141) |
| OV-3 | Widgets: alerts, queue across own groups, bookings today (counts), recent changes, running tasks, system status, my role | L | No PII in any fragment (PI-2 covers them); empty states per widget | built (#150); alerts wait for OV-6 |
| OV-4 | Server-rendered SVG charts (bar, horizontal bar above 4 items, line, pie) with text summary and table in `<details>` | M | No chart dependency; values readable without colour | built (#153) |
| OV-5 | `static/overview-live.js`: poll, `204` or fragments, one polite live region, no focus moves; CSP `script-src 'self'` on this page only | M | Under 150 lines, no dependency; abuse case "poll flood" against the rate limit | open |
| OV-6 | Alerts: banner until resolved, close per alert, pop-ups ≥ 8 s with pause, level filter, click opens the log entry | M | WCAG 2.2.1 and 2.2.2 checked in the manual pass; settings of ADR 0061 in the registry and `docs/settings.md` | open |
| OV-7 | Queue thresholds `QUEUE_WAIT_TARGET_MINUTES`, `QUEUE_WAIT_WARNING_MINUTES` (class D) | S | Warning alert raised once per crossing; test with the fake clock | open |

## 6. Personal layout — ADR 0062 (after ADR 0046)

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| LY-1 | Column `staff_members.overview_layout`, schema `openplanner.layout/1`, server-side validation | M | Reversible migration tested up and down; forbidden widgets dropped on save | open |
| LY-2 | *Customise overview* form without script: show/hide, up/down, size, height, view, rows, auto-scroll, columns, restore default | M | Every control works with post/redirect/get and keyboard only | open |
| LY-3 | Same controls live with the ADR 0061 script; pointer resize as an extra, never the only way | S | WCAG 2.5.7 checked; changes announced | open |
| LY-4 | `OVERVIEW_LOCKED_WIDGETS` (class D) and the lock in the form | S | Lock changes audited; layout changes not | open |

## 7. Branding page refresh — ADR 0041, O5

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| BR-1 | Branding tab on the organisation page (ADR 0047 §3) with live contrast check (≥ 4.5:1 against white, save blocked below), preview of a citizen page, "Appointments via OpenPlanner" toggle | M | Server repeats the contrast check; SVG upload sanitised or refused; abuse case "script in uploaded SVG" with a test | open |
| BR-2 | Organisation logo shown next to the organisation in lists and the filter | S | Image served from our origin with the correct content type; no inline SVG from uploads | open |

## 8. Page width — ADR 0080

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| PW-1 | Two page widths declared per screen in the register, `data-width` on `<main>`, tokens in `admin.css`, forms capped in wide pages, the week as seven columns or one row per day | M | Register, markup and stylesheet tests; `docs/admin-map.md` shows the width; checked at 375, 768, 1280, 1440 and 1920 px | built (#215) |
| PW-2 | Move further list screens (organisations, holidays, closures) to wide once a table on them needs the room | S | One line per screen in the register; admin shell test at 1280 px stays green | open |
| PW-3 | Playwright check of the wide page at 1920 px (content 96 rem, week in seven columns) in `admin-shell.spec.ts` | S | Runs against the Keycloak login of the existing spec | open |

## 9. Release — ADR 0052

| ID | Item | Size | Done when | Status |
|---|---|---|---|---|
| RL-1 | Release after each finished section above (`make release-plan`, release PR, tag on the product owner's instruction) | S | `CHANGELOG.md` names the new screens; manual topics live in the portal | open |
| RL-2 | Manual pass NVDA + Firefox and VoiceOver + Safari on the new shell and overview, recorded in `docs/a11y-audit.md` | M | Findings fixed or listed with an owner | open |
