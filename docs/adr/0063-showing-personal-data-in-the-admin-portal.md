# 0063. Showing personal data in the admin portal

Status: accepted (2026-10-30), requested by the product owner. Builds on the privacy rules in CLAUDE.md and ADR 0039 (auditor never sees personal data). Does not change the list of PII fields. Built, checked against the code on 2026-10-30 (status was *proposed*; `docs/adr-status.md`).

## Context

The product owner asked for a "PII marking per field" so that marked fields never appear on overview pages. CLAUDE.md fixes the PII fields as a constant (`name`, `email`, `phone`, `note`, `guest_join_url`, `host_join_url`, only on `Booking`) and says fixed rules such as PII fields stay constants. A configurable marking would let an installation switch a protection off, which is never allowed from the portal (ADR 0042). What the request needs is a clear, testable rule for where the existing PII fields may be shown, and a way to see that rule in the portal.

## Decision

1. **The PII field list stays a constant** in `app/domain/privacy.py`, with a classification per field: *direct* (name, email, phone) or *sensitive* (note, join URLs). There is no setting to change it.
2. **Overview and list pages never show PII.** That covers the overview and its widgets, cross-object lists, the audit log, exports, search results and alert texts. A test renders every list template with a booking full of marker values and fails if a marker appears.
3. **Search cannot reach PII.** List search and filters never match on PII fields, so a result cannot reveal who someone is.
4. **PII appears only on the booking detail page** (`/admin/groups/{slug}/bookings/{id}`) and in the queue item, for roles with `bookings.view` or `queue.work`, and never for the auditor (ADR 0039).
5. **Exports** (audit CSV/JSON, configuration export) never contain PII fields. Actor names in the audit log are admin subjects, not citizen PII, and follow ADR 0034.
6. **Visible in the portal.** *Platform → Advanced settings → Data fields* shows the constant list with its classification, read-only, for `settings.platform.view`. It explains the rule in plain words and links to the manual.

## Settings

None.

**Fixed on purpose:** the PII field list, its classification and the rules of §2–§5. They are protections, so they cannot be settings (ADR 0042).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; backlog section 4 | #130 | merged |
| 1 | PI-1: `app/domain/privacy.py` with the fixed field list and classification | #143 | merged |
| 2 | PI-2: marker-value test over every list, overview, export and alert template | #143 | merged |
| 3 | PI-3: list search and filters never match PII fields; abuse case in the threat model | #143 | merged |
| 4 | PI-4: read-only card *Platform → Advanced settings → Data fields*; manual topic | #143 | merged |

## Consequences

- New: `app/domain/privacy.py` (classification), the marker-value template test, a read-only card under Advanced settings.
- The design's editable "PII marking per field" is replaced by this read-only card.
- Threat model: abuse case "PII through list search or export" with a test.
- `docs/dataflow.md`: no change in flows; the rule is referenced in the DPIA input.
- Build note (PR 4): the booking detail page shows the reference, the call status, the
  answers to the group's form and the timeline; the group's own agenda, queue and planner
  pages show the first name (ADR 0043, 0044). The sweep allows exactly that and nothing
  else; the `note` field is reserved (a free-text answer lives in `form_answers`).
- Manual: topic *Data fields and personal data*.
