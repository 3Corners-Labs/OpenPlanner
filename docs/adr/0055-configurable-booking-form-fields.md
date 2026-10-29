# 0055. Configurable booking form fields with validation and a PII tag

Status: accepted (2026-10-29), requested and accepted by the product owner (fields per group, name split for all groups, `phonenumbers` with E.164, at most 5 fields by default). Amends HANDOVER B15 and the
CLAUDE.md privacy rule "PII fields: name, email, phone, … Only on `Booking`".

## Context

The booking form asks a fixed set: name, email and (per group hidden/optional/required) phone
(HANDOVER B15, `PhoneMode`). Groups need more: first and last name apart, and extra questions
such as a customer number, a postcode or a choice of topic. Administrators should add those
fields themselves, mark them required or not, and tag them as personal data so the rest of the
system can hide them from screens, logs, exports and outbound payloads.

Every field an administrator adds is a new place where a citizen's data can end up. The risks
are known from form builders in other products:

- **Wrong or missing classification**: a field holding personal data is tagged "not PII" and
  shows up in logs, exports or messages.
- **Weak or dangerous validation**: free regular expressions are hard to get right and can be
  abused for catastrophic backtracking (ReDoS).
- **Special categories and identifiers**: a well-meant field "BSN" or "medical reason" breaks
  the GDPR (art. 9, art. 87 / UAVG art. 46) and B15.
- **Changing a field after bookings exist**: renaming or retyping a field makes stored values
  meaningless, or silently drops them from purging.
- **Accessibility**: labels, error links, `autocomplete` tokens (WCAG 1.3.5) and translations
  are forgotten for custom fields.

The usual answer in mature products (government form builders, CRM custom fields, Typeform-like
tools) is the same: a **closed catalogue of field types**, each with its own built-in
validation; a **classification per field that defaults to the strictest value**; values stored
**next to the record, under a stable key**; and **one enforcement point** instead of every
consumer checking the tag itself. This ADR applies that pattern.

## Decision

1. **System fields and custom fields.** The form has system fields with fixed meaning and code
   behind them: `first_name`, `last_name`, `email`, `phone`. Their validation and PII tag cannot
   be changed. Names and email are always required (email carries the confirmation and the
   management link); the phone stays the group's *hidden / optional / required* setting on the
   *Settings* tab. Custom fields are added per group, at most `booking_form_max_custom_fields`.
2. **Closed catalogue of types.** A custom field has one type from a fixed list, each with a
   validator and normaliser in `app/domain/form_fields.py`: `short_text` (≤ 100 characters),
   `long_text` (≤ 1000; adding one is the group's explicit choice for free text that CLAUDE.md
   asks for), `email`, `phone`, `number` (whole numbers, min/max; identifiers with leading
   zeros are `short_text`), `date` (min/max in days relative to the day of booking),
   `postcode_nl`, `choice` (one of ≤ 30 option keys, so the stored value is never free text)
   and `checkbox` (for example consent; *required* means *must be ticked*). No
   administrator-defined regular expressions. A new type is a code change with tests.
3. **Validation is one function.** `validate_answers(form, raw, today, region)` in the domain
   runs on every path that accepts answers (the pages `/g/{slug}/book` and `/g/{slug}/direct`
   and the widget API, all through `BookingService`). Control characters are refused,
   whitespace is normalised, lengths are bounded. Unknown keys are refused under the field
   name `unknown`, so a caller's key never reaches an error or log. Email: the structural check
   shared with `Contact`. Phone (system field and custom fields): parsed with `phonenumbers`
   (Apache-2.0, the Python port of Google's libphonenumber) against the organisation's
   `phone_default_region` and stored as E.164. Errors carry a code and the field key, never the
   value.
4. **PII tag with a safe default.** Each custom field carries `classification`: `pii`
   (default) or `not_pii`. `email`, `phone` and `long_text` are always `pii`. Tagging a field
   `not_pii` needs an explicit confirmation by the administrator (a checkbox *the answers never
   say anything about a person*), and the change is audited by key. A free-text reason was
   considered and rejected: it would put an administrator's typing in the audit log, where
   personal data must not go. Raising a field to `pii`, or retiring it, rewrites the stored
   answers of the group at once so they count as PII (`Transaction.raise_answers_to_pii`).
5. **Forbidden content.** A key, label, help text or option label that matches a blocklist
   (BSN, burgerservice, sofi, paspoort, identiteitsbewijs, medisch, diagnose, gezondheid,
   religie, geloof, etnisch, afkomst, … in Dutch and English) is refused with an explanation.
   This is a guard rail, not a guarantee; the manual and the audit trail carry the rest.
6. **Storage next to the booking.** Answers are stored in one JSON column
   `bookings.form_answers` with the form version they were given under and the keys that were
   `not_pii` then. It joins the PII columns: `purge_pii` sets it to NULL whatever the tags say,
   so a wrong tag can never keep data past retention. The answers live on `Contact` and are
   purged with it. The CLAUDE.md privacy rule names the column.
7. **One enforcement point for the tag.** Consumers never read the tag themselves. The domain
   offers `Answers.visible(purpose)` (and `labelled_answers` for screens):

   | Purpose | Returns |
   |---|---|
   | `planner_view` | all answers: the booking page of the agenda, behind `bookings.view` (a PII permission, ADR 0039) |
   | `agent_view`, `export`, `message_placeholder`, `audit`, `log` | `not_pii` answers only; an agent in the own queue sees first names only (ADR 0043) |
   | `dispatch` | nothing, until a further ADR (CLAUDE.md: only first name and host join URL leave the system) |

   A new purpose is added to this table in code and here; there is no default branch. `Answers`
   keeps its values out of `repr`, and the log masker masks `form_answers`, `answers`,
   `first_name` and `last_name` at any depth.
8. **Immutable keys, versioned definitions.** A field's `key` and `type` never change after
   it is saved; changing them means retiring the field and adding a new one. A retired field's
   key is never used again in the group. Every change bumps `groups.form_version`. Retired
   fields keep their labels, so earlier answers stay readable until they are purged.
9. **Accessible by construction.** Every field renders through one template
   (`_contact_fields.html`) and one widget function (`form.js`) with a visible label,
   `aria-describedby` for help and error, the right input type and an `autocomplete` token
   (`given-name`, `family-name`, `email`, `tel`, `postal-code`; `off` for custom questions). A
   refused answer names its question in the error summary. Labels and help texts are stored
   per locale with the field (like contact blocks, ADR 0037); saving needs both `nl` and `en`;
   the citizen sees the request locale, else Dutch. The demo group has two optional example
   fields, so the keyboard and axe runs cover a select and a checkbox.
10. **Admin place (ADR 0047).** Group → *Settings* → *Booking form*
    (`/admin/groups/{slug}/settings/form`), a sub-page of the settings tab with the same
    permission `group.edit` (organisation admin and superuser). No new tab, section or
    permission.
11. **Name split** (all groups). `Contact.name` becomes `first_name` + `last_name`; stored
    names are migrated as *first word → first name, rest → last name*. `Contact.name` remains
    as the joined full name. The widget API accepts `first_name` and `last_name`; the old
    `name` is still accepted and split the same way, so `/api/v1` does not break.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `booking_form_max_custom_fields` | C platform | 5 | 0–15 | `settings.platform.manage` | 0: no new fields anywhere; a lower limit keeps existing fields editable |
| `phone_default_region` | D organisation | `NL` | the regions `phonenumbers` knows | `settings.organisation.manage` | Country of numbers typed without `+`/`00` |
| The fields themselves (type, required, classification, texts, options, bounds) | per group | none | point 2 | `group.edit` | Data of the group, not a `Settings` field |

**Fixed on purpose:** the type catalogue and each type's length limits (100, 1000, 30 options,
12 digits; validation rules, not tuning), the widget API's caps on the request body (15
answers, 1001 characters), the blocklist (a protection), the rule that `email`, `phone` and
`long_text` are always PII, and that purge ignores the tag (a protection cannot be switched
off).

## Consequences

- Migration 0053: `group_form_fields`, `groups.form_version`, `bookings.first_name`/`last_name`
  replacing `name`, `bookings.form_answers`. Reversible; the downgrade joins the name again
  and drops the answers.
- `docs/dataflow.md`, `docs/threat-model.md` (abuse cases: oversized answers, unknown keys, a
  field used to collect a BSN, a field tagged `not_pii` that holds PII) and `docs/widget.md`
  are updated.
- Dependency: `phonenumbers` (Apache-2.0, actively maintained, de-facto standard), recorded in
  `docs/dependencies.md`. Stored phone numbers are E.164 from now on; numbers stored before
  keep their old form until purged.
- Tests: table-driven tests per type for the validators (Hypothesis is MPL-2.0, outside the
  allowed licences); every purpose in point 7; purge clears `form_answers`; the migration both
  ways; pages, widget API and admin screen end to end.
- Built in one pull request in five steps: (1) domain types and validators, (2) storage, purge,
  masking and settings, (3) admin screen, (4) the pages and the widget, (5) the booking page
  in the agenda and the claimed booking in the queue. There is no booking export yet; when one
  comes it uses `Purpose.EXPORT`.
