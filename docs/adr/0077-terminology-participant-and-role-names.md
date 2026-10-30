# 0077. Terminology: "participant", and organisation names for staff functions

Status: proposed (2026-10-30), requested by the product owner. Extends ADR 0004 (texts), ADR
0039 (roles) and ADR 0074 (organisation text overrides, PR #168). Changes no right: ADR 0014
stays as it is. Nothing is built; the rename is part 2 and waits for the product owner's
acceptance.

## Context

OpenPlanner is a generic group-booking platform. A government contact center is the first
client, not the product. Yet the catalogues, the docs and the code call the person who books,
receives the mails and joins the call "burger" (Dutch) and "citizen" (English), and
`CLAUDE.md` describes the product as a government contact center. On 2026-10-30 the product
owner chose **"deelnemer" (English: "participant")**: more generic than "burger" or
"gebruiker".

The state on `main` at 2026-10-30, counted with a case-insensitive search for `burger` and
`citizen`:

| Area | Lines | Note |
|---|---|---|
| `app/i18n/locales/*.po` | 144 | both locales; "deelnemer" and "participant" are already used next to "burger" and "citizen" (11 files contain "deelnemer"), so the texts are mixed today |
| `app/` code and comments | about 400 | `app/domain` 209, `app/api` 63, `app/admin` 57, `app/web` 33, `app/adapters` 24, `app/jobs` 6; no template has a hit (texts come from the catalogue) |
| `docs/` | 544 | `docs/manual` 169 (nl and en), `docs/adr` 224, `docs/threat-model.md` 42, `docs/dataflow.md` 24, `docs/architecture.md` 28 |
| `tests/`, `e2e/`, `scripts/` | 167, 33, 22 | tests assert literal Dutch texts |
| `migrations/` | 25 | merged, never edited (CLAUDE.md) |
| `widget/`, `CLAUDE.md`, `CONTRIBUTING.md` | 1, 2, 1 | |
| `docs/HANDOVER.md` | 3 | Dutch stakeholder document |

"Participant" is already the word for the same person where the Webex integration needs one
(`EventKind.PARTICIPANT_LEFT`, the Webex resource `meetingParticipants`, "Deelnemer kwam
binnen" in the call timeline). The rename removes a synonym; it does not add one.

Some of the 'citizen' words are not text but contracts: a database column with its form field and
audit-event field name, and i18n keys (some stored as organisation and group overrides). A
rename of those needs a migration or breaks stored data, so the decision draws a line between
what changes and what stays.

Second need: a member of staff can hold several functions (administrator, superuser, planner,
handler, group admin, and so on). ADR 0014 gives each built-in role a stable code and
ADR 0039 lets the superuser make custom roles with their own names. But an organisation
cannot call the role the matrix names "Agent" (nl: "Medewerker") a "behandelaar" in its own
portal, and ADR 0074 deliberately limits the editable keys to participant pages and
messages.

## Decision

### The word

1. **"Deelnemer" (nl) and "participant" (en)** name the person who books, receives the mails
   and joins the call. Plural: "deelnemers" and "participants". It is used in every
   user-facing text of the catalogues (`nl.po`, `en.po`), the widget, the mail layouts, the
   admin manual, the docs and, where it is cheap and safe, in code identifiers and comments
   (point 3). "Burger", "citizen", "gebruiker" and "user" no longer name this person in text
   we write.
2. **What the word does not replace.** The *booking* is still an "afspraak" / "appointment".
   Staff stay "medewerker" / "agent" (see points 8 to 14) and the Webex host stays "host".
   Sentences that address the person directly keep "u" / "you"; "deelnemer" is for the third
   person and for headings. Words that merely contain the string are left alone:
   "burgerservicenummer" (the BSN rules), "burgerlijk", and a client's own organisation name.
   The Webex terms `meetingParticipants` and `participant` in Webex payloads are Webex's,
   not ours. In the Webex Contact Center integration "customer" stays where the provider says
   so.
3. **What changes in code and what stays.** Changes, because they are internal (part 2d):
   Python identifiers, module names and comments (`app/api/citizen.py`, `citizen_page`,
   `CitizenState`, `CitizenAction`, `Family.CITIZEN`, `CITIZEN_ACTIONS`, test names and
   helpers), and the codes `citizen.*` of the participant actions (`citizen.book`,
   `citizen.booking_view`, …), which become `participant.*`. Those codes are only labels in
   `CITIZEN_ACTIONS` and `docs/authorization.md`: no permission, role row, audit event or
   `authz.decision` log carries them (checked 2026-10-30), so the change needs no migration.
   Changes in its own step with a migration (step 1a, point 3a): the column below. Stays:
   - **Stable i18n keys** (`booking.citizen…`, `admin.queue.citizen.*`, `admin.matrix.citizen`,
     `authz.citizen.*`): only the texts change, never the keys (CLAUDE.md, ADR 0004). Keys on
     the editable list of ADR 0074 are stored in `organisation_texts` and `group_texts`;
     renaming them would need a data migration for no gain.
   - **`/api/v1` and the widget interface**: no field, path or attribute contains the word
     (checked 2026-10-30), so nothing breaks and nothing needs a version change. If one is
     ever found, it stays and gets a new name only through an ADR (CLAUDE.md).
   - **Merged migrations and the text of earlier ADRs** are never rewritten. This ADR is the
     key: read "citizen" and "burger" in an ADR before 0077 as "participant".

   3a. **The column `citizen_change_lead_minutes`** on `groups` (with the check constraint
   `citizen_lead`, the form field and the audit-event field name) becomes
   `participant_change_lead_minutes`. The product owner decided on 2026-10-30 that this
   happens as soon as possible, before the text rename (step 1a, first in line, in its own
   PR): the impact is already large, and every installation, audit event, log-platform query
   and export made later raises the cost. The migration is reversible (SQLite batch: rename
   the column, recreate the constraint as `participant_lead`). Audit events already written
   keep the old field name in their detail, so the audit view maps both names to the same
   label for good, because history is not rewritten. The i18n keys of the label
   (`admin.field.citizen_change_lead_minutes` and its hint and error keys) stay (point 3).

4. **`docs/HANDOVER.md` is not rewritten or translated.** Its three uses of "burger" stay as
   written; it is the stakeholder document of its time. It gets one added line at the top, in
   Dutch, pointing to this ADR (decided by the product owner on 2026-10-30; part 2c).
5. **Threat model and data flow.** `docs/threat-model.md` and `docs/dataflow.md` are
   engineering documents, not stakeholder history: their prose, headings and diagram labels
   change from "citizen" to "participant". Abuse-case numbers, flow ids and the names of the
   tests they cite do not change, so existing references stay valid; where a cited test is
   renamed in part 2d the reference is updated in the same PR.
6. **A guard against drift.** After part 2 a test (`tests/test_terminology.py`) fails when
   "burger" or "citizen" appears in the catalogue texts, templates, the widget or `docs/`
   (outside HANDOVER, `docs/adr/` before 0077, and a short allow-list of the stable names in
   point 3 and the words in point 2). That is how "no new uses" stays true after the rename.
   The product owner had no preference (2026-10-30); the recommendation, to fail CI rather
   than warn, is taken and can be loosened with one line.

### CLAUDE.md

7. **The one-line description** becomes: "Lightweight group-booking platform: organisations offer
   sessions to groups of participants, who book on a standalone page or an embeddable widget
   and join by video. Admin UI with IdP roles, Webex video links, Webex Contact Center task
   dispatch via Webex Connect, strict PII retention. The first client is a government contact
   center."
   In *Delivery surface*, the client's website "links to it after its own decision tree" stays;
   the sentence "we are the end station" stays; "citizens" becomes "participants" in the
   two other places (the OIDC line "participants never log in" and the manual line
   "participants get help in the page itself"). The rest of `CLAUDE.md` is unchanged, including
   its quality gates and the "government" mentions that are about the client's requirements
   (PingFederate, WCAG, DPIA). This change makes that edit to `CLAUDE.md` together with the ADR, so the product owner
   reviews wording and decision at once; if the ADR is not accepted, the edit goes with it.

### Staff functions and their names

A member of staff holds one or more roles; the principal carries a set of roles and
`decide` takes the union (ADR 0014, 0039). The code of a role (`org_admin`, `agent`, a custom
role's id) and the permissions it holds are rights. Its **name** is only a label.

8. **One term, `agent`, with a display word per organisation and per group.** The person who
   takes the video call is the *agent* in code, docs, manual and the matrix (`Role.AGENT`,
   ADR 0043). That stays the one internal name. What participants and staff *read* is the
   **agent term**: a singular and a plural per locale, for example nl "behandelaar" /
   "behandelaars", "medewerker" / "medewerkers" or "agent" / "agenten". An organisation sets it
   once; a group may override it (the word differs per team or subject). The chain is that of
   ADR 0074 §4 with the group first: group term → organisation term → catalogue of the locale
   → Dutch catalogue. It is stored in `organisation_texts` (ADR 0074) and `group_texts`
   (ADR 0022) under the stable keys `term.agent` and `term.agents`: no new table, no migration.
   The planner is not touched: it is a separate role, a custom role of the superuser
   (ADR 0044, ADR 0039), and the term never names it.
9. **Where the term appears.** (a) The portal: the role's label (**My rights**, matrix, top bar,
   staff list) and every portal text that means the person taking the call ("Aantal
   medewerkers", "Te weinig medewerkers"). (b) Texts to participants (pages, mails, SMS) and
   the organisation's and group's own texts (ADR 0074, ADR 0022), through the placeholders
   `{agent}` and `{agents}`, and `{Agent}` and `{Agents}` where the word starts a sentence or a
   label. "Een behandelaar neemt contact met u op" is then the catalogue text "Een {agent}
   neemt contact met u op". The placeholders join the allow-lists of ADR 0074 and ADR 0009
   for the keys whose catalogue text uses them. Grammar rule for the catalogue, checked by a
   test: no indefinite or definite article directly before the tag in English ("a/an/the
   {agent}"), and in Dutch only "een" (all three example words are *de*-words, but an
   organisation may choose a *het*-word, and "een" is right for both).
10. **Other role names.** `org_admin` and `group_admin` get an organisation display name per
    locale under `authz.role.org_admin` and `authz.role.group_admin` in `organisation_texts`
    (no group override, no term, no placeholder). Not renamable now: `superuser`, `auditor` and
    custom roles; leaving that out costs nothing structurally, since the renamable roles are a
    list in code and a name is a key in the tables above (another role is one more key, no
    migration). A custom role already has names of its own (ADR 0039 §3).
11. **The rights are out of reach by construction.**
    - `app/domain/authorization.py` knows role codes, never words; it does not import `app.i18n`.
      `MATRIX`, `decide` and `scope_for` read no label. A test loads overrides and asserts the
      decision for every role × permission is unchanged.
    - A term is display only. The audit log, `authz.decision` events, the role claim, the
      exports and `docs/authorization.md` always carry the code or the standard name. Wherever a
      chosen name is shown next to rights (the matrix column, **My rights**) the standard name
      stays visible as a second line, for every role alike, the superuser included.
12. **Rules for a word** (same machinery as `message_texts.check_text`): 2 to 40 characters,
    one line, no control characters, no placeholders; singular and plural are saved together or
    left blank (blank means the layer below). A term or role name may not equal (ignoring case)
    the name of another role in the same scope and locale, or the standard name of another role,
    so "Organisatiebeheerder" cannot be put on the agent. Plain text, escaped on output.
13. **Which screen shows what, and who edits.** The name belongs to the role and follows the
    screen's context: a group page uses the group's chain, an organisation page that
    organisation's; a screen without context (top bar, **My rights**) uses the organisation
    when the person has exactly one in scope and the standard words otherwise. Today roles and
    scopes are separate claims; when ADR 0049 is built, **My rights** can show each role with
    the name of the organisation it is held in. Edited with permissions that already exist, so
    no new permission: the organisation's term and role names with `texts.organisation.manage`
    in a section **Terms and function names** on *Organisation settings → Texts*; the group's
    agent term with `texts.manage` (the group admin) in the group's *Texts*. No new section,
    entry or tab (ADR 0047). A change needs a recent login, the Fetch Metadata check and writes
    an audit event with the scope, locale and key, never the words.
14. **Built when.** Not in part 2, which only rewrites words. Two steps, both after #168
    has merged, and the placeholders after the rename so the two do not fight over the same
    catalogue lines: step 4a, the term and the role names in the portal (storage, resolver,
    editor sections, tests); step 4b, `{agent}` and `{agents}` in the catalogue texts and mails
    (about 56 Dutch and 52 English lines mention medewerker or agent today), with the grammar
    test. Until then a custom role (superuser, ADR 0039) is the way to have a role called
    "Behandelaar" in the portal.

### Alternatives not chosen

- *Rename the built-in label for everyone* ("Medewerker" becomes "Behandelaar" in the
  catalogue). One word for every organisation, while organisations differ. Rejected; the
  standard stays "Medewerker".
- *A new table `organisation_role_names`.* Same result, one more migration, cache and
  invalidation path than the one ADR 0074 builds. Rejected.
- *Organisation admins make custom roles.* Contradicts ADR 0039 B1 and B2 (the superuser keeps
  the catalogue); it would let an organisation widen rights. Rejected.
- *Naming a person's combination of roles* (a "functieprofiel"). The union of roles is already
  what `decide` does; the product owner does not want it (Q6).


## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| – | – | – | – | – | None added. |

Checked against ADR 0042: the rename has no tunable value. The term and the role names (points 8 to 13)
are data under the existing permissions `texts.organisation.manage` and `texts.manage`, not a setting: whether an
organisation may name a role is not a threshold or a toggle, and the name changes no protection.
**Fixed on purpose:** the name length of 2 to 40 characters follows from the matrix column
and top bar layout (the same reason as the length limits of ADR 0074); the set of renamable
roles (`org_admin`, `group_admin`) and the one term (`agent`) are code, because it is a statement about which
labels may differ from the standard (point 9), not about policy.

## Decisions of the product owner and open questions

Answered by the product owner on 2026-10-30 (the points above already follow them):

| # | Question | Decided |
|---|---|---|
| Q1 | Is "Medewerker" renamed for everyone? | No. Per organisation, overridable per group or subject; "behandelaar", "agent" or "medewerker" are the examples. The agent takes the video call; the planner is a separate role (points 8 and 10). |
| Q2 | May auditor, superuser and custom roles be renamed per organisation? | Not needed now. The impact of leaving it out is none structurally (point 10). |
| Q3 | Which organisation counts for a person with roles in several? | The name belongs to the role and follows the screen's context (point 13). |
| Q4 | Which permission edits role names? | No separate permission; the existing texts permissions (point 13). |
| Q5 | Standard name beside the chosen one, also for the superuser? | The superuser is a role like any other: the same rule for all (point 11). |
| Q6 | A "functieprofiel" (a named combination of roles)? | No. |
| Q7 | A pointer line in HANDOVER? | Yes, one line (point 4). |
| Q10 | Does the function name also appear in texts to participants? | Yes, configurable, through a replaceable tag (`{agent}`), so "een behandelaar neemt contact op" (point 9). |
| Q11 | May a group rename the agent only, or also the group admin? | The group admin does it, for the agent term (point 13). Read this way; confirm in Q12. |
| Q8 | Rename the column `citizen_change_lead_minutes` before go-live? | Yes, as soon as possible: the impact is already large and grows with every change (point 3a). No go-live date was given, so the step does not wait for one. |
| Q9 | Does the guard test fail CI? | No preference; the recommendation, fail, is taken (point 6). |

Still open; none is guessed in the code:

- **Q12.** Two readings to confirm. Q3: a name per scope (group, organisation), never a choice
  among several organisations. Q11: "alleen de groepsbeheerder" means that the *group admin* is
  the one who may set the group's agent term, not that a group may rename its group admin role.
- **Q13.** What is the standard word when nobody sets one: nl "Medewerker" and en "Agent" as
  today, or "Agent" in both? "Agent" is the internal name and avoids confusion with the
  planner and group admin, who are also *medewerkers*; but in Dutch a participant of a
  government client may read "een agent" as police. Recommendation: keep nl "Medewerker", en
  "Agent". One catalogue line either way.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1a | Column `citizen_change_lead_minutes` → `participant_change_lead_minutes`, migration, audit alias (point 3a) | | decided, first in line |
| 1b | This ADR and the `CLAUDE.md` wording (point 7) | | in review |
| 2a | `nl.po` and `en.po` with the tests that assert their texts, `widget/`, `docs/widget.md` (keys unchanged) | | not started, waits for acceptance and for the open PRs on the catalogues |
| 2b | Admin manual `docs/manual/{nl,en}/` | | not started |
| 2c | `docs/*.md` except HANDOVER (which gets its one line), and `CONTRIBUTING.md` | | not started |
| 2d | Code identifiers, comments and the `citizen.*` codes; `e2e/`, `scripts/diagrams/` | | not started |
| 3 | Guard test `tests/test_terminology.py` | | not started, after 2a to 2d |
| 4a | The agent term and role names per organisation and group in the portal (points 8, 10 to 13) | | not started, after #168 |
| 4b | `{agent}` and `{agents}` in catalogue texts and mails, grammar test (point 9) | | not started, after 2a and 4a |

## Consequences

- One word in every text and document we write. The old words remain only in history
  (earlier ADRs, merged migrations, HANDOVER) and in the stable names of point 3.
- Part 2 is large in line count and small in risk: texts and comments, no behaviour. It touches
  the same files as every open PR on catalogues, docs and templates (at 2026-10-30: #164, #168,
  #170 and others). Each step rebases on `main` before it opens, and 2a waits until those
  PRs have merged.
- Tests that assert Dutch texts are updated with the text, never weakened. `e2e/pages.json`
  is checked in 2d.
- `docs/architecture.md` gets a short glossary (deelnemer, medewerker, host, afspraak) in part 2c.
- The term and role names (step 4), when built, add keys to the allow-list of ADR 0074, a section to its
  editor, a manual topic in nl and en, a line in the threat model (a role name must not be used
  to mislead; mitigated by the standard name shown beside it) and a row in `docs/audit-events.md`.
  `docs/authorization.md` does not change, because rights do not change.
- Risk: a find-and-replace on "citizen" would damage the stable names of point 3. Part 2 is
  done by review of each hunk, not by a global substitution, and the guard test has the
  allow-list.
