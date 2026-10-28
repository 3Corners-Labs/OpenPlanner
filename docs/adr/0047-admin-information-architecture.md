# 0047. Admin information architecture: scope, object, tab, and one navigation register

Status: proposed (2026-10-28), requested by the product owner. Builds on ADR 0014 and 0039
(authorisation matrix, adjustable roles), ADR 0016 (HTMX only where needed), ADR 0033
(context-sensitive help and manual) and ADR 0042 (settings registry). Leaves the portal's logo
open (see *Open points*).

## Context

The admin portal grew one screen per feature. Each screen got its own menu entry or its own
group tab, wherever it was convenient at the time. The result works, but it does not read as
one product, and configuring something means jumping between menus:

- **Thirteen flat menu entries** (overview, audit, retention, rights, matrix, integrations,
  organisations, branding, holidays, closures, API, updates, help) without any grouping.
- **One organisation lives in five places.** Branding (`/admin/branding`), holidays
  (`/admin/holidays`), closures (`/admin/closures`) and channels (under *Integrations*) each
  start with "choose an organisation". Contacts (`/admin/organisations/{org}/contacts`) has no
  menu entry at all. Setting up a new organisation means visiting five menus and picking the
  same organisation five times.
- **Nine flat group tabs** (overview, settings, staffing, exceptions, holidays, reminders,
  messages, agenda, closure). Related tabs are not grouped: staffing, exceptions and holidays
  are all "when are we open"; reminders and texts are both "what does the citizen receive".
  The contact block for a closure hides under *Closure*, and the integration check under
  *Settings*.
- **Integrations mixes scopes.** The page shows platform things (Webex Service App, WxCC
  registrations, webhooks) next to organisation things (email and SMS channels).
- **Rights and matrix** are two menu entries for one topic.
- **Nothing stops the next screen** from adding a fourteenth entry or a tenth tab. ADR 0042
  (advanced settings, system status), ADR 0044 (agenda) and ADR 0046 (staff directory, PR
  #61) all add screens.

The product owner wants the portal to feel like Webex Control Hub, which contact-center staff
already know. That means the *structure* of Control Hub, not its brand. Control Hub has a
grouped side navigation, list pages with search, object pages with tabs, and settings grouped
by topic within the object they belong to. The portal stays server-rendered without JavaScript
(CLAUDE.md, ADR 0016) and keeps its own look and accent colour. Cisco's fonts, logos and
colours are not used.

## Decision

### 1. Every screen has one home: scope → object → tab

Every admin screen and every setting belongs to exactly one **scope**:

| Scope | Object | Who typically works there |
|---|---|---|
| **Platform** | the installation | superuser |
| **Organisation** | one organisation | org admin (own), superuser |
| **Group** | one group | group admin, planner, org admin |

A screen is reached through its object, never through its own top-level entry. A setting that
can exist on several scopes (for example a default on the organisation and an override on the
group) lives on each scope's own tab under the **same topic name**. It is never a separate
screen that asks "which organisation?".

### 2. Side navigation: three sections

The side navigation shows sections with entries. An entry is either a list of objects or a
cross-object view:

| Section (nl / en) | Entries |
|---|---|
| **Bewaken / Monitor** | Overview (status and to-dos), Audit log, Retention |
| **Beheren / Manage** | Groups, Organisations, Staff (ADR 0046), Access (rights + matrix as tabs) |
| **Platform / Platform** | Connections (Webex, WxCC, webhooks), API clients, Holiday calendars, Advanced settings (ADR 0042), System and updates |

Help, the language switch and the profile (name, roles, log out) move to the top bar. The
profile is a `<details>` disclosure, so it works without JavaScript.

An entry shows only if the matrix grants its permission (ADR 0014). A section with no visible
entries is not shown. For a user who can see only one organisation, *Organisations* opens that
organisation directly instead of a list of one.

### 3. Object pages: tabs and sub-tabs

An object page has a page header (breadcrumbs, name, status chips, primary action) and at most
**six** top-level tabs. Related screens become **sub-tabs** of one tab.

**Organisation:**

| Tab | Holds (today's screen) |
|---|---|
| General | name, time zone, time format and other class D settings (ADR 0042) |
| Branding | `/admin/organisations/{org}/branding` |
| Channels | email and SMS (`/admin/organisations/{org}/channels`, now under *Integrations*) |
| Contact | `/admin/organisations/{org}/contacts` |
| Holidays | `/admin/organisations/{org}/holidays` |
| Closure | `/admin/organisations/{org}/closure` |

**Group:**

| Tab | Sub-tabs (today's screen) |
|---|---|
| Overview | readiness checklist (§5), upcoming exceptions, shortfalls (ADR 0044) |
| Agenda | day and week view (ADR 0044) |
| Planning | Staffing · Exceptions · Holidays |
| Messages | Reminders · Texts · Contact (now under *Closure*) |
| Settings | general settings, connection check (now a button under *Settings*) |
| Closure | closure, bookings on hold |

A tab or sub-tab shows only if the matrix grants its permission. An object page needs at least
one visible tab.

### 4. Cross-object views are entry points, not second homes

Lists that span objects (all active closures, all groups, all organisations) are **lists**.
They have a search field and filters (GET form), a count, a table with status chips, and a link
to the object's tab. They never contain their own edit forms. The current top-level pages
*Branding*, *Holidays* and *Closures* become such lists or disappear:

- *Closures* becomes a filter on *Overview* ("active closures") that links to the object's
  *Closure* tab.
- *Branding* and the organisation pick on *Holidays* disappear. Shared holiday calendars stay
  under *Platform → Holiday calendars*.

Old URLs answer with a permanent redirect (308) to their new home, so bookmarks and existing
manual links keep working. The redirect target comes from the register (§6). It is never taken
from the request. Each redirect declares the same permission as its target and builds the
location only from the authorised object's slug, as the old `/hours` redirects already do
(`docs/threat-model.md`).

### 5. Config flow: checklist, inheritance in place, no round trips

- **Readiness checklist.** The overview tab of a group and of an organisation shows what is
  still missing before it can take bookings. For a group, that is staffing, texts, channels and
  the connection check (the check of `check_routes.py`). For an organisation, it is
  channels, branding and contact. Each item links straight to the tab that fixes it and shows
  "done" when it is done. The checklist is computed from data and needs no new state.
- **Inheritance shown in place.** Where a group value falls back to the organisation (or an
  organisation value to the platform default, ADR 0042), the group's tab shows the effective
  value with "taken from organisation" and lets the user override it in place. Going to the
  organisation is a link, not a requirement.
- **Back to where you were.** A link that leaves a page to fix something elsewhere carries a
  `return` key, not a URL. The key names a register entry plus the object's slug (for example
  `group.planning.staffing:paspoort`). The target page validates the key against the register
  and the matrix, then offers "back to …" after saving. No open redirects.
- **Settings grouped by topic.** Every setting in the ADR 0042 registry gets a mandatory
  `topic` that names one tab or sub-tab of this register. The settings forms are rendered per
  topic, so settings of the same kind always sit together and in the same order.

### 6. One navigation register in code

A new module `app/web/navigation.py` (shared HTML plumbing, next to `templates.py`) declares
the whole structure once:

- **sections**, **entries**, **object pages**, **tabs** and **sub-tabs**;
- for each: a stable key (`group.planning.staffing`), the i18n label key, the route template,
  the permission (from the matrix), the icon, the manual topic (ADR 0033) and, for old routes,
  the redirect source.

The side navigation, the object tabs, the breadcrumbs, the `return` keys (§5), the redirects
(§4) and the manual's table of contents are all rendered from it. `NAVIGATION` in
`app/admin/pages.py` and `TABS` in `app/admin/group_routes.py` are replaced by it. The
register decides **where** a screen lives. It never grants rights: visibility still comes only
from the matrix.

`python -m app.cli admin-map` prints the register as Markdown to `docs/admin-map.md`. CI fails
if the file differs from the code, as for `docs/authorization.md`.

### 7. Guarding the structure for new screens

Five checks keep the structure from drifting:

1. **Route test.** A test enumerates every `GET` route under `/admin` that returns HTML. Each
   one must be a register entry, tab or sub-tab, or be declared as a *sub-page* of one (detail
   pages such as `/admin/groups/{slug}/bookings/{id}`, edit forms such as
   `/staffing/{rule_id}`). A new screen that is not placed fails CI.
2. **Shape test.** At most three sections, at most six tabs per object page, every tab and
   sub-tab has a manual topic and an `nl` and `en` label.
3. **Settings test.** Every setting in the ADR 0042 registry has a `topic` that resolves to a
   register entry on its scope (class C → platform, class D → organisation).
4. **Review question 7** in CLAUDE.md: *"Which screen or setting is new or moved, where does it
   live in the admin structure (scope, object, tab), and why does it fit there? If it needs a
   new section, entry or top-level tab: which ADR changes the structure?"* The `security-review`
   skill checks that the answer matches the diff.
5. **Changing the structure needs an ADR.** A new section, a new top-level entry or a seventh
   tab is a structural change: one ADR, then the register. Adding a sub-tab or a setting to an
   existing topic is not, and only needs question 7 answered.

### 8. Page patterns and look

Every admin page uses one of three patterns:

- **list** (search, count, table, row link);
- **object** (header, tabs, sub-tabs);
- **form** (sections per topic, one primary button at the bottom).

The visual refresh follows Control Hub's calm style: a narrow top bar, a grouped side
navigation, a white page header band with tabs, and content on a light grey canvas. It stays
within `admin.css` tokens and keeps the WCAG 2.2 AA rules of CLAUDE.md: contrast 4.5:1 for
text and 3:1 for the focus ring, targets of at least 24×24 px, colour never the only signal,
and `prefers-contrast` and `prefers-reduced-motion` honoured. No JavaScript is needed: tabs are
links with `aria-current`, and the profile and filters are `<details>` or GET forms.

## Settings

None. The structure in this ADR is not tunable per installation or organisation: a portal
whose menu differs per installation cannot have one manual.

**Fixed on purpose:**

- the three sections and the limit of six top-level tabs per object, as a design rule (§7.2);
- the redirect status 308 for old URLs, a protocol constant.

The readiness checklist (§5) uses existing data and adds no thresholds.

## Consequences

- **Fewer round trips.** Setting up an organisation becomes one page with six tabs instead of
  five menus. A group goes from nine tabs to six, with sub-tabs.
- **Code:** the new `app/web/navigation.py` and the `admin-map` CLI command. `pages.py`,
  `group_routes.py` and the templates `layout.html`, `_page_header.html` and
  `_group_header.html` render from the register. Route handlers and permissions stay as they
  are. Only paths move, with 308 redirects from the old ones.
- **i18n:** new keys for section, tab and sub-tab labels and for the checklist, in `nl` and
  `en`.
- **Manual (ADR 0033):** topics follow tabs. Moved screens keep their topic, and the manual's
  table of contents follows the register.
- **Docs:** `docs/admin-map.md` (generated), plus a short section in `docs/architecture.md` on
  the register's place between `app.web` and the routes.
- **Tests:** the route, shape and settings tests of §7. The authorisation tests are unchanged
  (same routes and permissions, plus redirects).
- **e2e:** `e2e/pages.json` gets the new paths. axe and Lighthouse run on the new shell in both
  locales.
- **Threat model:** the `return` key (§5) and the redirects (§4) are new inputs. Both resolve
  only through the register, never to a URL from the request. An abuse case "open redirect via
  return or legacy path" with a test is added to `docs/threat-model.md`.
- **Process:** CLAUDE.md gets review question 7, and the `security-review` skill checks it.
- **No migration.** No database change.

## Build order

1. **This ADR,** plus review question 7 in CLAUDE.md and the skill (docs only).
2. **Register and shell.** `app/web/navigation.py`, the side navigation with sections, the top
   bar with profile, the page header with tabs, the route and shape tests, and `admin-map`.
   Paths stay the same.
3. **Organisation page.** Six tabs, with *Branding*, *Holidays* and *Closures* removed from the
   menu, channels moved from *Integrations*, and 308 redirects.
4. **Group page.** Six tabs with sub-tabs, contact moved to *Messages*, the connection check
   moved to *Settings*.
5. **Overview and lists.** The readiness checklist, active closures, and the list pattern
   (search, count, table) for groups, organisations, API clients and the audit log.
6. **ADR 0042 registry `topic`** and the settings test, when the registry lands.

## Open points

- **Logo.** The portal keeps the neutral "OP" mark until the product owner decides on a logo.
  It is not the organisation's logo: organisation branding (O5) applies to citizen pages only.
- **Agenda across groups.** A planner with several groups may want one agenda for all of them
  (*Monitor → Agenda*). This is left out until ADR 0044 is built and used.

## Assumptions to confirm

1. The section names *Bewaken / Beheren / Platform* (en: *Monitor / Manage / Platform*).
2. The contact block for a closure moves from *Closure* to *Messages → Contact*. The closure
   page keeps a link to it.
3. An org admin with one organisation goes straight to that organisation's page from
   *Organisations*.
4. Old URLs redirect permanently (308) for at least one release and are then removed by a
   later ADR.
