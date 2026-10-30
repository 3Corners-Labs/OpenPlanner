# 0004. Internationalisation as a self-contained module

Status: accepted (2026-10-23). Supersedes the `*_nl` / `*_en` columns in HANDOVER §3. Partly built, checked on 2026-10-31: the module, catalogues, negotiation and organisation overrides (ADR 0074) run; the global override layer and `.po` export and import are not built (`docs/adr-status.md`).

## Context

The public pages, widget, emails, SMS and admin UI are multilingual by design: Dutch by
default, English required, more languages possible later. Visitors pick a language or get one
detected from their browser. Organisations want to adjust texts themselves, preferably in the
admin UI, without a code release. `CLAUDE.md` already requires gettext with `nl` and `en`
entries for every string.

HANDOVER §3 models translatable group content as fixed columns (`title_nl`, `title_en`,
`email_intro_nl`, ...). That hard-codes two languages into the schema.

## Decision

### 1. One package, `app/i18n/`, with its own public interface

- **Core (framework-free):** locale negotiation, the lookup chain, placeholder validation and
  safe formatting. Pure Python, unit-tested like the domain.
- **Integrations:** the request locale dependency and the page renderer live in `app/web/`, a
  shared layer that both server-rendered entry points (`app/api` pages, `app/admin`) use. The
  widget catalogue endpoint will live in `app/api`.
- **Override storage:** the core defines an `OverrideSource` Protocol; the database adapter
  (`app/adapters/db/`) implements it.
- import-linter: `app.i18n` imports nothing from `app.api`, `app.admin`, `app.jobs` or
  `app.adapters`. The domain does not import `app.i18n`: domain errors carry codes
  (`slot_full`), and the edges map them to keys (`error.slot_full`).

### 2. Stable message keys, not source sentences

Messages are identified by dotted keys in English (`booking.form.email.label`), not by the
English sentence. This way admin overrides and added languages survive copy changes, and every
locale, English included, is an ordinary catalogue. Code stays English; no user-facing sentence
appears in code.

### 3. Shipped catalogues in gettext format

`app/i18n/locales/<locale>.po`, read with [Babel](https://babel.pocoo.org/) (BSD-3-Clause) at
startup. No compile step and no writable filesystem are needed. Translators can use any gettext
tool (Poedit, Weblate). Fuzzy and empty entries fall back to Dutch. Babel will also provide
plural rules and locale-aware date/time formatting. Plural entries are rejected until plural
support is built (with the first message that needs it). Accept-Language parsing is a small
pure function of our own, because it must also reject malformed tags.
`tests/i18n/test_catalogues.py` fails when a key is missing from `nl` or `en`, a key in code
is not in the catalogues, a catalogue key is unused, or placeholders differ between locales.

### 4. Lookup chain (first hit wins)

1. Organisation override (database)
2. Global override (database, superuser)
3. Shipped catalogue for the requested locale
4. Shipped catalogue for `nl`
5. The key itself; `i18n.missing_key` is logged once per key and locale (key only)

### 5. Locale negotiation (pure function, first match wins)

Standalone pages and admin UI:
1. `?lang=xx` (from the language switcher). This also sets the `lang` cookie: a functional
   preference cookie (1 year, `Secure`, `SameSite=Lax`) that needs no consent.
2. The `lang` cookie.
3. `Accept-Language`, weighted by `q`, falling back from region to language (`en-GB` → `en`).
4. The organisation's default locale.
5. `nl`.

Only enabled locales can win. Widget: `lang` attribute → choice made inside the widget
(in memory, no storage) → host page `<html lang>` → `navigator.languages` → `nl`. The widget
gets its strings from `GET /api/v1/i18n/{locale}?ns=widget` (public, cacheable, ETag = revision).

A booking stores its locale. Emails, SMS, reminders and the `locale` field sent to Webex
Connect use it.

### 6. Accessibility

`<html lang>` follows the resolved locale (WCAG 3.1.1). Every page has a language switcher. Each
option is labelled in its own language (`Nederlands`, `English`) and carries its own `lang`
attribute (3.1.2). Switching language keeps entered form data (3.3.7).

### 7. Editing in the admin UI (work package 7)

- **Superuser:** enable or disable locales; add a locale (untranslated keys fall back to `nl`,
  completeness shown in percent); edit global overrides; export and import `.po` files for
  professional translators.
- **org_admin:** overrides for their own organisation, scoped by `require_scope`.
- **Group content** (page title, intro, email intro/outro, duration label) moves from fixed
  columns to a locale-keyed table `group_text(group_id, field, locale, text)`. Same fallback:
  requested locale → `nl`.

### 8. Security

- Override and group texts are plain text. They are always escaped on output (Jinja2
  autoescape, `textContent` in the widget, plain-text mail part). Markup lives in templates,
  never in translations.
- Placeholders are `{name}` only. An override must use exactly the placeholders of the shipped
  string. Formatting uses a restricted formatter without attribute or index access, which
  rules out format-string injection such as `{x.__class__}`.
- Length limit per text (2000 characters). Every change writes an audit event (actor, key,
  locale).

### 9. Caching

Each process caches catalogues and overrides. A revision counter in the database is bumped on
every change and checked at most every 5 seconds, so web and worker converge within 5 seconds
without a restart.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Core, `nl`/`en` catalogues, negotiation, switcher (§1–§6) | #1, #19, #85 | built |
| 2 | Group texts (`group_texts`, §7 last bullet) | #29 | built |
| 3 | Organisation overrides: store, cached source, editor (§4 step 1, §7 org_admin, §9) | #168 | in PR |
| 4 | Global (superuser) overrides, enable locales, `.po` export and import, revision counter (§4 step 2, §7) | | follow-up |

## Consequences

- Work package 2 models group texts as `group_text` instead of `*_nl` / `*_en` columns.
- Built ahead of work package 3 (2026-10-23): the core, the `nl`/`en` catalogues,
  negotiation, the switcher and the home page `/`. Work package 3 adds the widget endpoint.
- The i18n core is held to the same bar as the domain: coverage ≥ 90% and mutation testing. Work package 4 uses the booking locale for mail and SMS. Work package 7 adds
  the admin editor.
- Adding a language needs no code change; making it complete in the shipped catalogues is a
  normal PR.
