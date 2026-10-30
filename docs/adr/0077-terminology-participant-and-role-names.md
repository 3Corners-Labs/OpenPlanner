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
   Staff stay "medewerker" / "agent" (see points 8 to 12) and the Webex host stays "host".
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
   Changes in its own step with a migration (step 5, point 3a): the column below. Stays:
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
   `participant_change_lead_minutes` in step 5, in its own PR, after the text rename. The
   product owner asked on 2026-10-30 until when this can still change: it is cheapest now.
   Only the test host runs; each installation, audit event, log-platform query and export
   made after go-live raises the cost. The migration is reversible (SQLite batch: rename the
   column, recreate the constraint as `participant_lead`). Audit events already written keep
   the old field name in their detail, so the audit view maps both names to the same label
   for good, because history is not rewritten. If go-live passes before step 5 is done, the
   name stays as it is (Q8).

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

8. **Display names per organisation, overridable per group.** An organisation may give
   `org_admin`, `group_admin` and `agent` its own display name per locale, and a group may
   give `agent` its own. The agent is the person who takes the video call; organisations call
   that person "behandelaar", "agent" or "medewerker" and like to be recognised by it, and the
   word can differ per team or subject. The names live in `organisation_texts` (ADR 0074) and
   `group_texts` (ADR 0022) under the stable keys `authz.role.org_admin`,
   `authz.role.group_admin` and `authz.role.agent`. No new table and no migration. The chain is
   that of ADR 0074 §4 with the group first: group name → organisation name → catalogue of
   the locale → Dutch catalogue. The standard names stay as they are today ("Medewerker",
   "Agent"). The **planner** is not touched: it is a separate role, a custom role made by the
   superuser (ADR 0044, ADR 0039), and a rename of `agent` never reaches it.
9. **The rights are out of reach by construction.**
   - `app/domain/authorization.py` knows role codes, never names; it does not import `app.i18n`.
     `MATRIX`, `decide` and `scope_for` read no label. A test loads an override and asserts the
     decision for every role × permission is unchanged.
   - Overrides are display only. The audit log, `authz.decision` events, the role claim, the
     exports and `docs/authorization.md` always carry the code or the standard name, so
     a reviewer or auditor can compare across organisations. Wherever a chosen name is shown
     next to rights (the matrix column, **My rights**) the standard name stays visible as a
     second line, for every role alike, the superuser included.
   - Not renamable now: `superuser`, `auditor` and custom roles (the product owner sees no need
     yet). Leaving the flexibility out costs nothing structurally: the editable roles are a
     list in code and a name is a key in the tables above, so another role is one more key,
     without a migration. A custom role already has names of its own (ADR 0039 §3).
10. **Rules for a name** (same machinery as `message_texts.check_text`): 2 to 40 characters,
    one line, no control characters, no placeholders, and not equal (ignoring case) to another
    role's name in the same scope and locale, or to the standard name of another role, so
    "Organisatiebeheerder" cannot be put on the agent. Blank means the name of the layer below.
    A name is plain text, escaped on output.
11. **Where it shows, and who edits.** The name belongs to the role and follows the context of
    the screen. A group page (agenda, queue, the group's staff) shows the group's chain; an
    organisation page that organisation's; a screen without context (top bar, **My rights**)
    uses the organisation when the person has exactly one in scope and the standard names
    otherwise. Today roles and scopes are separate claims; when ADR 0049 (role and scope in
    one claim) is built, **My rights** can show each role with the name of the organisation it
    is held in. Edited with the permissions that already exist for texts, so no new
    permission: the organisation's names with `texts.organisation.manage` (organisation admin
    for the own organisations, superuser for all) in a section **Function names** on
    *Organisation settings → Texts*; the group's `agent` name with `texts.manage` in the
    group's *Texts*. No new section, entry or tab (ADR 0047). A change needs a recent login,
    the Fetch Metadata check and writes an audit event with the scope, locale and role code,
    never the free text.
12. **Built when.** Not in part 2, which only rewrites words. The names are a feature with an
    allow-list, an editor section and tests, and they build on #168, which is in review. They
    get their own PR after #168 has merged. The word in texts addressed to participants
    ("the first free medewerker") does not change with them (Q10). Until then a custom role
    (superuser, ADR 0039) is the way to have a role called "Behandelaar".


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

Checked against ADR 0042: the rename has no tunable value. The role names (points 8 to 11)
are data under the existing permissions `texts.organisation.manage` and `texts.manage`, not a setting: whether an
organisation may name a role is not a threshold or a toggle, and the name changes no protection.
**Fixed on purpose:** the name length of 2 to 40 characters follows from the matrix column
and top bar layout (the same reason as the length limits of ADR 0074); the set of overridable
roles (`org_admin`, `group_admin`, `agent`; at group level `agent`) is code, because it is a statement about which
labels may differ from the standard (point 9), not about policy.

## Decisions of the product owner and open questions

Answered by the product owner on 2026-10-30 (the points above already follow them):

| # | Question | Decided |
|---|---|---|
| Q1 | Is "Medewerker" renamed for everyone? | No. Per organisation, overridable per group or subject; "behandelaar", "agent" or "medewerker" are the examples. The agent takes the video call; the planner is a separate role (point 8). |
| Q2 | May auditor, superuser and custom roles be renamed per organisation? | Not needed now. The impact of leaving it out is none structurally (point 9). |
| Q3 | Which organisation counts for a person with roles in several? | The name belongs to the role and follows the screen's context (point 11). |
| Q4 | Which permission edits role names? | No separate permission; the existing texts permissions (point 11). |
| Q5 | Standard name beside the chosen one, also for the superuser? | The superuser is a role like any other: the same rule for all (point 9). |
| Q6 | A "functieprofiel" (a named combination of roles)? | No. |
| Q7 | A pointer line in HANDOVER? | Yes, one line (point 4). |
| Q9 | Does the guard test fail CI? | No preference; the recommendation, fail, is taken (point 6). |

Still open; none is guessed in the code:

- **Q8.** Rename `citizen_change_lead_minutes` (step 5) now, before go-live? The `citizen.*` codes
  are renamed in any case (point 3). Recommendation: yes. When is go-live?
- **Q10.** Should the agent's function name also appear in texts addressed to participants and
  in the mails ("een medewerker neemt contact op")? Dutch articles are the same for all three
  words; English ("a handler", "an agent") is not. This ADR leaves participant texts as they
  are and builds the portal first. Recommendation: later, as a `{agent}` placeholder.
- **Q11.** Is `agent` the only role a group may rename (point 8), or also `group_admin`?
- **Q12.** Is the reading of Q3 right: a name per scope, no choice among several organisations?

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | This ADR and the `CLAUDE.md` wording (point 7) | | in review |
| 2a | `nl.po` and `en.po` with the tests that assert their texts, `widget/`, `docs/widget.md` (keys unchanged) | | not started, waits for acceptance and for the open PRs on the catalogues |
| 2b | Admin manual `docs/manual/{nl,en}/` | | not started |
| 2c | `docs/*.md` except HANDOVER (which gets its one line), and `CONTRIBUTING.md` | | not started |
| 2d | Code identifiers, comments and the `citizen.*` codes; `e2e/`, `scripts/diagrams/` | | not started |
| 3 | Guard test `tests/test_terminology.py` | | not started, after 2a to 2d |
| 4 | Function names per organisation and group (points 8 to 11) | | not started, after #168 |
| 5 | Column `citizen_change_lead_minutes` → `participant_change_lead_minutes`, migration, audit alias (point 3a) | | not started, depends on Q8 |

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
- Role names (step 4), when built, add keys to the allow-list of ADR 0074, a section to its
  editor, a manual topic in nl and en, a line in the threat model (a role name must not be used
  to mislead; mitigated by the standard name shown beside it) and a row in `docs/audit-events.md`.
  `docs/authorization.md` does not change, because rights do not change.
- Risk: a find-and-replace on "citizen" would damage the stable names of point 3. Part 2 is
  done by review of each hunk, not by a global substitution, and the guard test has the
  allow-list.
