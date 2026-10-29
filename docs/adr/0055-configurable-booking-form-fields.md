# 0055. Configurable booking form fields with validation and a PII tag

Status: accepted (2026-10-29), requested and accepted by the product owner. Amends HANDOVER B15 and the
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
   be changed; only "hidden / optional / required" where the domain allows it (`email` always
   required, it is the channel for confirmation and the management link). Custom fields are
   added per group, at most `booking_form_max_custom_fields`.
2. **Closed catalogue of types.** A custom field has one type from a fixed list, each with a
   validator and normaliser in `app/domain/form_fields.py`:
   `short_text` (≤ 100 chars), `long_text` (≤ 1000, only if the group enables free text, as
   today), `email`, `phone`, `number` (whole numbers, min/max; identifiers with leading zeros are `short_text`), `date` (min/max relative to today),
   `postcode_nl`, `choice` (single select from an admin list of ≤ 30 option keys; labels are
   i18n text blocks, so the stored value is never free text), `checkbox`
   (e.g. consent; "required" means "must be ticked"). No admin-defined regular expressions.
   A new type is a code change with tests, not a setting.
3. **Validation is one function.** `validate_answers(definition, raw) -> Answers` in the domain
   runs on every path that accepts answers (widget API, `/g/{slug}`, reschedule, agent API).
   All control characters are rejected, whitespace is normalised, lengths are bounded.
   Unknown keys are refused under the field name `unknown`, so an attacker's key never reaches
   an error or log. Email: the existing structural check, shared with `Contact`. Phone: parsed
   with `phonenumbers` (Apache-2.0, port of Google's libphonenumber) against the organisation's
   default region and stored as E.164. Errors carry a code and the field key, never the value.
4. **PII tag with a safe default.** Each custom field carries `classification`:
   `pii` (default) or `not_pii`. Lowering to `not_pii` needs the permission
   `group.form.classify` (org admin and up), a mandatory reason, and is audited.
   Types `email` and `phone` are always `pii` and cannot be lowered. The admin screen explains
   in its manual topic what counts as personal data, with examples.
5. **Forbidden content.** A field's label or key that matches a blocklist (BSN, burgerservice,
   sofi, paspoort, identiteitsbewijs, medisch, diagnose, religie, geloof, etnisch, …; nl and
   en) is refused with an explanation. This is a guard rail, not a guarantee; the audit trail
   and the manual carry the rest.
6. **Storage next to the booking.** Answers are stored in one JSON column `Booking.form_answers`,
   keyed by the field's immutable `key`, together with the definition version they were given
   under. `form_answers` joins the PII columns: `purge_pii` sets it to NULL for every booking,
   whatever the tags say, so a wrong tag can never keep data past retention. The CLAUDE.md
   privacy rule is amended to name this column.
7. **One enforcement point for the tag.** Consumers never read the tag themselves. The domain
   offers `Answers.visible(purpose)`:

   | Purpose | Returns |
   |---|---|
   | `agent_view`, `planner_view` | all answers (authorised screens, the agent needs them) |
   | `export`, `message_placeholder`, `audit`, `log` | `not_pii` answers only |
   | `dispatch` | nothing, until a further ADR (CLAUDE.md: only first name and host join URL leave the system) |

   A new purpose is added to this table in code and in this ADR; there is no default branch. The `Answers` type has `repr=False`
   values, and the log masker in `app/logging.py` masks the whole `form_answers` key.
8. **Immutable keys, versioned definitions.** A field's `key` and `type` never change after
   it is saved; changing them means adding a new field and retiring the old one. Every change
   to the form bumps `form_version`; bookings keep the version they were made under, so the
   agent view shows the label the citizen saw. Retired fields stay readable until purge.
9. **Accessible by construction.** Every field renders through one macro and one widget
   component with a visible label, `aria-describedby` for help and error, the right input
   `type`/`inputmode` and an `autocomplete` token derived from the type (system fields:
   `given-name`, `family-name`, `email`, `tel`). Label and help text are i18n text blocks
   per group; saving requires both `nl` and `en`.
10. **Admin place (ADR 0047).** Group → tab *Settings* → sub-topic *Booking form*, next to the
    current phone mode (which moves there). No new tab or section.
11. **Name split** (accepted for all groups). `Contact.name` becomes `first_name` + `last_name`; the existing name is
    migrated as "first word → first_name, rest → last_name". `Contact.first_name` keeps its
    meaning for WxCC.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `booking_form_max_custom_fields` | C platform | 5 | 0–15 | `settings.platform.manage` | 0 switches custom fields off for all groups |
| `phone_default_region` | D organisation | `NL` | ISO 3166-1 alpha-2 regions known to `phonenumbers` | `settings.organisation.manage` | Region for numbers typed without `+`/`00` |
| Field definition (type, required, classification, label, help, options) | D group | none | catalogue in point 2 | `group.form.manage` / `group.form.classify` | Per group, not a `Settings` field |

**Fixed on purpose:** the type catalogue and each type's length limits (validation rules, not
tuning), the blocklist (a protection), the rule that `email`/`phone` are always PII, and that
purge ignores the tag (a protection cannot be switched off).

## Consequences

- One migration: `bookings.form_answers` (JSON, nullable), `bookings.form_version`,
  `bookings.first_name`/`last_name` replacing `name`; a `group_form_fields` table. Reversible.
- `docs/dataflow.md`, `docs/threat-model.md` (abuse cases: oversized answers, control
  characters, a field used to collect a BSN, a field tagged `not_pii` that holds PII) and the
  DPIA input are updated.
- Dependency: `phonenumbers` (Apache-2.0, actively maintained, de-facto standard), recorded
  in `docs/dependencies.md`.
- Tests: table-driven tests per type for the validators (Hypothesis is MPL-2.0, outside the
  allowed licences); a test that every
  purpose in point 7 drops `pii` values where required; a test that `purge_pii` clears
  `form_answers`; the Playwright keyboard/axe run gets a group with one field of each type.
- Build steps: (1) domain types and validators, (2) storage, purge and masking, (3) admin
  screen, (4) widget and `/g/{slug}`, (5) agent/planner views and export.
