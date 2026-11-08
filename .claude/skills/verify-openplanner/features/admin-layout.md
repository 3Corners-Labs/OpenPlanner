# Admin layout on a laptop screen

Staff open the admin portal on an ordinary laptop: 1280 × 800 px, the full menu on the left.
Every page keeps its content inside its own box: a long address wraps inside its fact, a status
that needs a sentence takes the line under its label, a form lists its options under each
other, and a table shows its last column without scrolling sideways. The texts read as the
settings form names them, without the team's design-record numbers.

## Sub-features

- `facts-wrap`: a fact without spaces (an issuer, a callback address) wraps inside its box on
  `/admin/idp-mapping` and `/admin/settings` instead of running over the fact beside it.
- `environment-name`: System status says `Omgeving: Ontwikkelomgeving`, the environment's name.
- `role-options`: `/admin/roles/new` lists its permissions under each other, not in one row.
- `status-wraps`: the overview's *Systeemstatus* puts a long state (the proxy finding of this
  stack) on the line under "Proxy" and wraps it inside the card, for `super` and `auditor`.
- `tables-fit`: the agenda's day and week, the rights matrix and the audit log fit at 1280 px.
- `texts`: the new-role page names no design record; the group overview says
  `Duur van een gesprek` and `Minimale aanlooptijd`, as the group settings do.

## How to get to it (user POV)

- Any admin page at a window of 1280 × 800 px with the menu expanded (the default at that
  width): Overzicht, Geavanceerde instellingen (Systeemstatus), Koppeling identiteitsprovider,
  Rechtenmatrix and Rollen aanpassen → Nieuwe rol, Auditlog, and Groepen → "Demo: videogesprek"
  with its tabs Overzicht and Agenda (day and Weekoverzicht).
- The overview as `auditor` (the compliance role), which shows the same status widget.

## Driving it with journey.mjs

Preconditions:

- A stack that is up. A booking of this run in `state.json` (from `booking`) puts a row in the
  agenda day; without one the journey takes the next working day, which has slots but no rows.
- The development users `super` and `auditor` (`dev/keycloak/README.md`).

- **Set the window.** The journey sets the viewport to 1280 × 800 before the first page.
- **Open each page.** Sign in as `super`, open `/admin/`, `/admin/settings`,
  `/admin/idp-mapping`, `/admin/roles/new`, `/admin/groups/demo`, the agenda day and week of
  the booking's day, `/admin/matrix`, `/admin/audit`; then in a new browser context as
  `auditor` open `/admin/`. Each page waits for its level 1 heading.
- **Measure.** In the page: the page may not scroll sideways; a table region (`.table-scroll`)
  may not be narrower than its table; no element outside a table region may end right of its
  nearest card, widget, fact (`.facts > div`) or `main`. Each finding names the element and the
  pixels it runs over.
- **Read the texts.** System status: the fact `Omgeving` reads `Ontwikkelomgeving`. New role:
  the main text has no `ADR NNNN`. Group overview: the facts `Duur van een gesprek` and
  `Minimale aanlooptijd` are there.
- **Proof.** One step per page, `admin-layout/NN-<page>.png` and `.aria.yml`; a page with a
  finding is logged as failed with its findings in `journey.jsonl`, and the journey fails after
  the last page, so every page keeps its evidence.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs admin-layout`

## Gotchas

- The width is what the faults depend on. At 1440 px most of them hide; at 1280 px with the
  menu collapsed to the rail (the top bar's menu button) the tables have 180 px more. The
  journey uses a fresh context, so the menu is expanded.
- The fonts decide by how much a table fits. The journey proves it in the browser of this
  machine (Google Chrome, or the `PLAYWRIGHT_CHROMIUM_CHANNEL` you set); the e2e suite checks
  the same pages in its own browsers and fonts.
- The proxy finding exists only because this stack's worker cannot reach 127.0.0.1 of the host
  (see *Stand-in versus a real installation* in `../SKILL.md`); it is the long status the
  overview must wrap. On a stack where the round trip succeeds, the row is absent and the step
  proves less.
- The audit log grows with every run of the journeys; its widest cell is the list of role values
  of a sign-in (`agent,auditor,group_admin,org_admin,superuser`), there from the first login.
- The agenda's week takes `?week=<day>`, not `?day=`.
