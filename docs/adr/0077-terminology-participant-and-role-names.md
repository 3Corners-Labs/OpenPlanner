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

Some of the 'citizen' words are not text but contracts: a database column and its setting
key, permission codes that custom roles and audit events carry, and i18n keys. A rename
would need a migration or break stored data, so the decision draws a line between what
changes and what stays.

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
   Staff stay "medewerker" / "agent" (see points 7 to 11) and the Webex host stays "host".
   Sentences that address the person directly keep "u" / "you"; "deelnemer" is for the third
   person and for headings. Words that merely contain the string are left alone:
   "burgerservicenummer" (the BSN rules), "burgerlijk", and a client's own organisation name.
   The Webex terms `meetingParticipants` and `participant` in Webex payloads are Webex's,
   not ours. In the Webex Contact Center integration "customer" stays where the provider says
   so.
3. **What changes in code (part 2e) and what stays.** Changes, because they are internal:
   Python identifiers, module names and comments (`app/api/citizen.py`, `citizen_page`,
   `CitizenState`, `CitizenAction`, `Family.CITIZEN`, `CITIZEN_ACTIONS`, test names and
   helpers). Stays, because a rename needs a migration, breaks stored data or a published
   contract, or is a stable key:
   - **Stable i18n keys** (`booking.citizen…`, `admin.queue.citizen.*`, `admin.matrix.citizen`,
     `authz.citizen.*`, `admin.field.citizen_change_lead_minutes`): only the texts change,
     never the keys (CLAUDE.md, ADR 0004).
   - **The column `citizen_change_lead_minutes`** on `groups`, with the check constraint
     `citizen_lead`, the same name as setting field, form field and audit-event field name.
     Renaming it needs a migration, and audit events already written carry the old name. It
     stays; the label next to it says "deelnemer".
   - **Permission codes `citizen.*`** (`citizen.book`, `citizen.booking_view`, …). They appear
     in `authz.decision` logs, audit history and `docs/authorization.md`. They stay as stable
     codes; the displayed names change.
   - **`/api/v1` and the widget interface**: no field, path or attribute contains the word
     (checked 2026-10-30), so nothing breaks and nothing needs a version change. If one is
     ever found, it stays and gets a new name only through an ADR (CLAUDE.md).
   - **Merged migrations and the text of earlier ADRs** are never rewritten. This ADR is the
     key: read "citizen" and "burger" in an ADR before 0077 as "participant".
4. **`docs/HANDOVER.md` is not rewritten or translated.** Its three uses of "burger" stay as
   written; it is the stakeholder document of its time. Whether it gets a one-line pointer to
   this ADR is open question Q7.
5. **Threat model and data flow.** `docs/threat-model.md` and `docs/dataflow.md` are
   engineering documents, not stakeholder history: their prose, headings and diagram labels
   change from "citizen" to "participant". Abuse-case numbers, flow ids and the names of the
   tests they cite do not change, so existing references stay valid; where a cited test is
   renamed in part 2e the reference is updated in the same PR.
6. **A guard against drift.** After part 2 a test (`tests/test_terminology.py`) fails when
   "burger" or "citizen" appears in the catalogue texts, templates, the widget or `docs/`
   (outside HANDOVER, `docs/adr/` before 0077, and a short allow-list of the stable names in
   point 3 and the words in point 2). That is how "no new uses" stays true after the rename.

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

8. **Recommendation: an organisation may give the built-in organisation, group and agent roles
   its own display name per locale; the label changes, the right never does.** The name lives
   in the existing `organisation_texts` table of ADR 0074 under the stable keys
   `authz.role.org_admin`, `authz.role.group_admin` and `authz.role.agent`. No new table and
   no migration. The lookup chain is the one of ADR 0074 §4 (organisation text → catalogue of
   the locale → Dutch catalogue). Example: an organisation calls `agent` "Behandelaar".
9. **The rights are out of reach by construction.**
   - `app/domain/authorization.py` knows role codes, never names; it does not import `app.i18n`.
     `MATRIX`, `decide` and `scope_for` read no label. A test loads an override and asserts the
     decision for every role × permission is unchanged.
   - Overrides are display only. The audit log, `authz.decision` events, the role claim, the
     exports and `docs/authorization.md` always carry the code or the standard name, so
     a reviewer or auditor can compare across organisations.
   - Where a chosen name is shown next to rights (the rights matrix column, **My rights**), the
     standard name stays visible as a second line, so an organisation cannot present a role as
     something it is not without it being visible.
   - The names of `superuser` (a platform role) and `auditor` (whose name states a privacy
     guarantee, ADR 0039 B7) cannot be overridden. Custom roles already have names of their
     own (ADR 0039 §3); they are not overridable per organisation.
10. **Rules for a name** (same machinery as `message_texts.check_text`): 2 to 40 characters,
    one line, no control characters, no placeholders, and not equal (ignoring case) to the
    name another role has in that organisation and locale, or to the standard name of another
    role, so "Organisatiebeheerder" cannot be put on the agent. Blank means the standard name.
    A name is plain text, escaped on output.
11. **Where it shows and who edits.** Shown in the admin portal wherever the organisation is
    known: the role names in the top bar, **My rights**, the rights matrix, the staff list and
    the role editor. The organisation is the one in the scope filter (ADR 0047, PR #161) when
    exactly one is selected, or the single organisation of the principal; otherwise the
    standard names are shown (open question Q3). Edited by the same permission as the texts,
    `texts.organisation.manage` (organisation admin for the own organisations, superuser for
    all; not group admin or auditor), on the same screen as ADR 0074: a section **Function
    names** on *Organisation settings → Texts*. No new section, entry or tab (ADR 0047). The
    change needs a recent login, the Fetch Metadata check and writes an audit event with the
    organisation, locale and role code, never the free text.
12. **Built when.** Not in part 2. Part 2 only rewrites words. The role names are a feature
    with a table of keys, an editor section and tests, and they build on #168, which is in
    review. They get their own PR after #168 has merged and after the product owner has
    answered Q1 to Q5. Until then a custom role (superuser, ADR 0039) is the way to have
    a role called "Behandelaar", with its own permissions.

### Alternatives not chosen

- *Rename the built-in label for everyone* ("Medewerker" becomes "Behandelaar" in the
  catalogue). Cheap, but it is one word for every organisation and it is the question Q1, not
  an organisation setting. It can still happen as a part 2a text change if the product owner
  wants it.
- *A new table `organisation_role_names`.* Same result, one more migration, cache and
  invalidation path than the one ADR 0074 builds. Rejected.
- *Organisation admins make custom roles.* Contradicts ADR 0039 B1 and B2 (the superuser keeps
  the catalogue); it would let an organisation widen rights. Rejected.
- *Naming a person's combination of roles* ("functieprofiel" as a bundle). The union of roles
  is already what `decide` does; a named bundle is a new concept with its own lifecycle. Not
  proposed; Q6.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| – | – | – | – | – | None added. |

Checked against ADR 0042: the rename has no tunable value. The role names (points 8 to 11)
are data under the existing permission `texts.organisation.manage`, not a setting: whether an
organisation may name a role is not a threshold or a toggle, and the name changes no protection.
**Fixed on purpose:** the name length of 2 to 40 characters follows from the matrix column
and top bar layout (the same reason as the length limits of ADR 0074); the set of overridable
roles (`org_admin`, `group_admin`, `agent`) is code, because it is a statement about which
labels may differ from the standard (point 9), not about policy.

## Open questions

The product owner has not decided these; none is guessed in the code.

- **Q1.** Is the Dutch standard name of `agent` ("Medewerker") to be "Behandelaar" for everyone,
  or only when an organisation chooses it (point 8)? The English standard "Agent" has the
  same question. Related: "Beheerder" for the organisation and group admin roles.
- **Q2.** Is the set in point 9 right: may `auditor` and `superuser` never be renamed, and
  may custom roles not be renamed per organisation?
- **Q3.** Which organisation counts when a person holds roles in several (point 11)? The
  recommendation is the one selected in the scope filter, and standard names otherwise.
- **Q4.** Is `texts.organisation.manage` the right permission, or should role names have a
  permission of their own (`role_names.organisation.manage`)? Recommendation: reuse, to keep
  the matrix small; a separate one is a one-line change later.
- **Q5.** Should the matrix page always show the standard name beside the chosen one (point 9),
  also for the superuser, who sees several organisations?
- **Q6.** Is naming a *combination* of roles for one person wanted (a "functieprofiel")? Not
  proposed here.
- **Q7.** `docs/HANDOVER.md`: leave it completely untouched, or add one line at the top
  pointing to this ADR? Recommendation: one line.
- **Q8.** `citizen_change_lead_minutes` and the `citizen.*` permission codes (point 3): leave
  as stable names for good, or rename them with a migration and an alias period in a later,
  separate ADR? Recommendation: leave. It costs a word in two places a user never sees.
- **Q9.** The guard test (point 6): fail CI on any new use, or warn only for the first release?
  Recommendation: fail.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | This ADR and the `CLAUDE.md` wording (point 7) | | in review |
| 2a | `nl.po` and `en.po` with the tests that assert their texts, `widget/`, `docs/widget.md` (keys unchanged) | | not started, waits for acceptance and for the open PRs on the catalogues |
| 2b | Admin manual `docs/manual/{nl,en}/` | | not started |
| 2c | `docs/*.md` except HANDOVER, and `CONTRIBUTING.md` | | not started |
| 2d | Code identifiers and comments that are not schema or API; `e2e/`, `scripts/diagrams/` | | not started |
| 3 | Guard test `tests/test_terminology.py` | | not started, after 2a to 2d |
| 4 | Function names per organisation (points 8 to 11) | | not started, after #168 and Q1 to Q5 |

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
