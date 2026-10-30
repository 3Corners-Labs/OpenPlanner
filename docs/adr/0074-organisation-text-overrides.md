# 0074. Organisation text overrides

Status: accepted (2026-10-30), built (step 1). Builds the organisation layer promised by ADR 0004
§4 and §7, ADR 0009 and ADR 0022 (which deferred the editor). The global (superuser) layer of
ADR 0004 and the `.po` export and import stay unbuilt.

## Context

ADR 0004, 0009 and 0022 promise that an organisation edits its own wording in the admin portal:
the lookup chain is group text → organisation override → catalogue, and `CLAUDE.md` says
overrides are editable in the admin UI. Only the group layer existed (`group_texts`, ADR 0022);
the translator had the `OverrideSource` Protocol and only `NoOverrides`. A check of every ADR
against the code on 2026-10-30 found the gap (`docs/backlog-adr-gaps.md`, AG-1).

Organisations want their own wording once, for all their groups: the introduction on the group
page, what a citizen reads after booking, the subject of the reminder.

## Decision

1. **A table.** `organisation_texts(organisation_id, locale, key, text)`, primary key on the
   first three, `ON DELETE CASCADE` (migration 0072, reversible). Plain text, no personal data.
2. **An allow-list of editable keys** (`app/domain/text_overrides.py`): every message block of
   ADR 0009 (with its own placeholder allow-list and length) and twelve citizen-page texts (the
   group introduction, the direct-call card and page, the pages after booking and to confirm).
   Everything else (buttons, labels, error messages, the admin portal) is not editable. A page
   text allows exactly the placeholders its page provides; `tests/i18n/test_editable_texts.py`
   checks that against the nl and en catalogues.
3. **The same rules as a group text**: not blank, within the key's length, no control characters,
   no line breaks in subjects and titles, `{placeholder}`s from the allow-list only, a form is
   saved whole or not at all. The rules live once, in `message_texts.check_text`. Texts are plain
   text and escaped on output (Jinja2 autoescape, the plain-text mail part); markup and Jinja
   are never editable (ADR 0009).
4. **Layer order**: group text → organisation text → catalogue of the locale → Dutch catalogue.
   `Translator.lookup` already asked an `OverrideSource` for the organisation; the group layer is
   applied by the message composer first.
5. **A cached `OverrideSource`** (`app/adapters/db/text_overrides.py`). The translator is
   synchronous, the database asynchronous, so the adapter keeps every organisation's texts in
   memory and re-reads the table at most every 5 seconds (`refresh`, called by the request
   locale dependency, `citizen_page` and the message runner). A process that saves a text
   invalidates its own copy, so the editor shows the change at once; the other process (web or
   worker) converges within 5 seconds. ADR 0004 §9 names a revision counter; reading the whole
   table (a few dozen rows per organisation at most) every 5 seconds gives the same bound
   without a second table, and a counter can come later if the table ever grows.
6. **Citizen pages** bind `_()` to the group's organisation in `citizen_page`, the one place all
   group pages pass. Messages already bound the translator to the group's organisation.
7. **The editor** is `/admin/organisations/{org}/texts/{locale}` with its own permission,
   `texts.organisation.manage` (organisation admin for the own organisations, superuser for all;
   not the group admin or the auditor), a recent login for changes, the Fetch Metadata check,
   and an audit event with the organisation, locale and key names, never the texts.
8. **Where it lives (ADR 0047).** The organisation page is not built yet; today an
   organisation's general settings are the entry *Organisation settings* (`/admin/organisation-settings`,
   `/admin/organisations/{org}/settings`). The editor is a sub-page of that entry (reached by a
   link on it, with breadcrumbs back), like the group's *Texts* sub-tab under *Messages*. No new
   section, entry or tab. **Assumption to confirm:** when the organisation page gets its tabs
   (ADR 0047 step 8), the editor moves to *General* → *Texts* or to a *Messages* tab shared with
   channels; the paths stay, because the register redirects.
9. **Not built here**: the global (superuser) override layer of ADR 0004 §4 and §7, `.po`
   export and import, locales added in the portal, and a revision counter. They need a decision
   from the product owner and are listed in `docs/adr-status.md`.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| – | – | – | – | – | None added. |

**Fixed on purpose:** the length limits are those of the group texts (subject 200, body 2000,
SMS 320 characters): they follow from the mail header and SMS segment limits and from the page
layout, not from policy. The 5-second cache bound is the one ADR 0004 §9 names. The number of
texts is bounded by the allow-list (about fifty keys per locale and organisation), so no limit
on rows is needed.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Table, allow-list, cached `OverrideSource`, wiring in pages and messages, editor, manual, threat model | #168 | in PR |
| 2 | Global (superuser) layer, `.po` export and import, revision counter | | follow-up |

## Consequences

- `docs/authorization.md` gains `texts.organisation.manage`; `docs/admin-map.md` two sub-pages.
- `docs/manual/{nl,en}/organisation-texts.md` explains the screen; the threat model has a flow.
- Every citizen request now reads from memory; at most one `SELECT` of a small table every
  5 seconds per process.
- A group that has its own text for a key never sees the organisation's text for it; the group
  editor shows the organisation's text as the default it replaces.
- An organisation text that a later release makes invalid (a placeholder dropped from the
  catalogue) would raise `KeyError` at formatting; the catalogue tests and the allow-list test
  keep the placeholders of a key stable, and a new placeholder is always optional.
