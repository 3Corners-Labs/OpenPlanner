# Admin UX audit: discoverability (first pass)

Method: heuristic evaluation (Nielsen's 10, Krug's trunk test) of the group admin screens, from the
templates and `docs/admin-map.md`. Not yet checked in a browser or with real admins; severity is
0 (none) to 4 (catastrophic). Scope of this pass: group Settings and the booking form sub-page, plus
a structural scan of every object page. Other screens (organisation, platform) are still open.

## Structural findings

| # | Finding | Heuristic | Sev |
|---|---|---|---|
| S1 | Group **Settings** is one long page: general fields, integrations, IdP key, booking-form link, health check and archive in a single scroll. Nothing tells the admin which topics exist. | Recognition over recall, minimalism | 3 |
| S2 | The **booking form** is a sub-page behind a text link at the bottom of Settings (`group_settings.html`, card `#booking-form`). It has no tab, no summary and no count of questions. An admin looking for "ask the participant a question" has no reason to open Settings. | Visibility, trunk test | 3 |
| S3 | **Archive group** is a sub-page behind a button at the bottom of Settings. Acceptable for a rare, destructive action, but the entry point is far from the group overview. | Findability | 1 |
| S4 | Group tabs: 10 on one object (Overview, Settings, Staffing, Exceptions, Holidays, Reminders, Message texts, Agenda, Queue, Closure), on two rows in the header. CLAUDE.md asks an ADR for a seventh tab, so check ADR 0047 for how this count was accepted; related topics are not grouped. | Too many choices | 2 |
| S5 | Sub-pages (`/settings/form/{key}`, `/staffing/{rule_id}`, `/exceptions/{id}`) show no "you are here" in the tab bar beyond the parent highlight, and the way back is a text link at the bottom ("Terug naar de instellingen"). | Orientation, user control | 2 |
| S6 | Every topic explanation is behind "Meer uitleg" (i) or the manual (ADR 0033). The page itself does not carry the answer. | Help and documentation | 2 |

## Booking form page (`group_form_fields.html`)

| # | Finding | Heuristic | Sev |
|---|---|---|---|
| B1 | **Key** is asked first and in technical terms (`description`, "Kleine letters, cijfers en _"). An admin types a question, not an identifier. Derive the key from the Dutch question and show it only under "advanced". | Match with real world | 3 |
| B2 | "Soort" (type) options say what the character limit is but not what the field is for; no example of when to use which. | Recognition | 2 |
| B3 | Six inputs plus a classification select and flags shown at once. Nl/en question and hint texts are four separate boxes. | Minimalist design | 2 |
| B4 | "Persoonsgegevens" is a select the admin must answer, while the type already decides it for free text ("altijd persoonsgegevens"). | Error prevention | 2 |
| B5 | Empty state is a sentence; no ready-made examples (customer number, reference, reason for appointment). | Help, efficiency | 2 |
| B6 | Reorder is two buttons per row (up, down) and "Uitzetten" (retire) sits next to them with no undo or explanation of what retiring does to old answers. | Control and freedom | 2 |
| B7 | The system fields (first name, last name, email, phone) are explained in one sentence above the table, not shown as rows. The admin cannot see the form as the participant sees it. | Visibility | 2 |

## Proposed fixes, in order

1. **Show the booking form on the group overview and as its own place in Settings navigation.**
   Preferred: a card "Booking form" on Settings with the list summary ("4 fixed + 1 own question", button
   "Manage questions"). An ADR 0047 decision; a new tab needs an ADR, a card does not. (S1, S2)
2. **Split Settings into titled sections with an in-page index** (General, Contact-centre link, Login,
   Booking form, Health check, Danger zone). No new tab. (S1, S3)
3. **Rewrite the add-field form**: question first, type with one-line description and example, key
   derived and hidden, personal-data flag preset from the type. Preview of the participant's form. (B1 to B5, B7)
4. **Put short help text in the page** and keep (i) for depth only. (S6)
5. **Breadcrumb inside object pages** (Group › Settings › Booking form). (S5)
6. Add an empty-state with examples and make "retire" reversible. (B5, B6)

## Open questions for the product owner

- Should the 10 group tabs be grouped (for example Schedule: staffing, exceptions, holidays; Messages:
  reminders, texts)? That is a new ADR 0047 sub-tab layer.
- Do admins want a "setup checklist" on a new group (name, hours, form, texts, publish)?
- Who should we test with? Five admins doing three tasks (add a question, change hours, close for a day)
  find most of these in a day.

## Status after the first fixes

Built: the booking form card (S2, #360), "On this page" on the group settings (S1, #400), the add
form with the question first and a derived technical name (B1, B3, B5, #403), the kind chosen first
with a proposed classification (B2, B4, #407). Open: S4, S5, S6, B6, B7.

## Organisation screens (second pass)

Method as above, from the templates, `docs/admin-map.md` and the Dutch texts; not yet checked in a
browser. The organisation has six tabs, and *Overview* carries four sub-tabs.

| # | Finding | Heuristic | Sev |
|---|---|---|---|
| O1 | **Four unrelated topics hide as sub-tabs of *Overzicht***: Instellingen, Teksten, Inbedding, Inloggen. An overview is where one looks for a summary, not for settings. On a group, *Instellingen* is a top tab, so the same word is at another level here. | Findability, consistency | 3 |
| O2 | **Organisation *Instellingen* is one flat table of 13 class D settings** (time format, time zone, footer, dormant staff days, shortfall horizon, rebooking days, rebooking reminder, closure retention and notice, phone region, three booking-confirmation values). No grouping, no "On this page", no word on which feature each belongs to. | Minimalist design, recognition | 3 |
| O3 | **Ways to log in have three homes**: providers on the organisation (*Inloggen*), the mapping in *Manage › Identity provider mapping*, and the key in each group's settings. Nothing answers "how do my staff log in?" in one place. (ADR 0083 explains it; the screens do not.) | Consistency, help | 3 |
| O4 | **What participants see is spread over three places**: *Huisstijl* (top tab), *Kanalen* (top tab) and *Teksten* (a sub-tab of Overzicht). The group repeats it with *Berichtteksten* and *Herinneringen*; the labels *Teksten* and *Berichtteksten* do not say how they relate (organisation default, group override). | Match with the real world | 2 |
| O5 | **Inbedding is a read-only list.** The lead tells the admin to switch it on "bij Groepsinstellingen › Insluiten op websites", and each row links to the group's settings top, not to that field. | Visibility, efficiency | 2 |
| O6 | **Contact** says nothing about what it is for; only the lead does ("how participants reach you when they cannot book"). It is shown on the closure page and the maintenance page, and it sits next to *Noodsluiting* that holds the same contacts (permission `organisation.closure`). | Match with the real world | 2 |
| O7 | **Two edit paths for the organisation itself**: the profile card on *Overzicht* (name, language: a small link) and the *Instellingen* sub-tab. | Consistency | 2 |
| O8 | **Feestdagen** (organisation) chooses calendars that *Platform › Holiday calendars* creates; one word for two jobs, and no link from the choice to the calendars. | Recognition | 1 |
| O9 | A new organisation has no next steps: groups, huisstijl, kanalen, teksten and login all start empty and nothing says what is needed before the first booking. | Help, visibility of status | 2 |

### Proposed fixes for the organisation

1. **Settings in topics with "On this page"** (O2): group the 13 settings under Formats and time,
   Planner and shortfalls, Closure, Booking confirmation, Contact; same pattern as ADR 0101 and
   the group settings. No new tab; the settings stay class D with the same bounds.
2. **Link *Inbedding* rows to the field** (O5): `.../settings#integrations`, which has an anchor
   since #400. A one-line change.
3. **Move the sub-tabs out of *Overzicht*** (O1, O4): *Instellingen* and *Teksten* become top tabs,
   *Inloggen* and *Inbedding* stay as sub-tabs of a new or existing tab. This is an ADR 0047
   change (more than six tabs on an object) and needs the product owner's decision.
4. **A login summary** (O3): one card on *Inloggen* that names the three places and links to each,
   no new setting.
5. **Name the purpose in the labels** (O4, O6): for example *Contact bij sluiting*, and a line on
   each text page saying whose default it is.
6. **Setup checklist on the organisation overview** (O9): see the open questions.

### Open questions for the product owner (organisation)

- Fix 3 changes the tab structure (ADR 0047). Which variant: *Instellingen* and *Teksten* as top
  tabs, or a new top tab *Deelnemers* that holds Huisstijl, Kanalen and Teksten?
- A setup checklist for a new organisation (O9): wanted, and shown to whom?
