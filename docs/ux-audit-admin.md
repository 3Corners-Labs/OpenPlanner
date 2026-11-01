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
