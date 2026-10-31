# 0009. Notification channels, reminder rules and message templates

Status: accepted (2026-10-23). Built; the organisation override layer came with ADR 0074 (#168, merged; checked on 2026-10-31, `docs/adr-status.md`).

## Context

Organisations want to configure when citizens receive reminders and through which channel:
email and SMS now, WhatsApp on the roadmap. They also want to edit message texts. HANDOVER
§3 hard-codes two reminders (email T−24 h, SMS T−2 h) and per-group `email_intro_nl/en`
columns.

## Decision

1. **Channels are data, not code paths.** `ChannelKind` (`email`, `sms`, `whatsapp`) is a
   domain enum. Each kind declares what it needs from a booking: an email address; a phone
   number; a phone number plus explicit consent (WhatsApp). Sending goes through one port,
   `MessageSender`, with one adapter per kind. A new channel is a new enum value, its
   requirements and an adapter; booking and planning code stay unchanged.
2. **WhatsApp is roadmap.** The enum value and its consent requirement exist, so the
   architecture is ready. No adapter is built and no group can enable it until the privacy
   officer approves (Meta is a third-country processor, B11; message templates need Meta
   approval; the booking form needs an opt-in).
3. **Reminder rules per group:** channel, offset before the start (in minutes), enabled. The
   domain turns them into jobs with a pinned start (CLAUDE.md). A reminder is skipped when its
   time has passed or the booking cannot be reached on that channel (no phone, no consent).
4. **Templates are text, not code.** Every message has a fixed, accessible layout (semantic
   HTML plus a plain-text part). Editable parts are text blocks:
   - defaults in the i18n catalogues (`message.<purpose>.<channel>.<block>`),
   - organisation overrides through the i18n override store (ADR 0004),
   - per-group intro and outro through `group_text`.
   Placeholders are `{name}` only, from a fixed allow-list per purpose (for example
   `{first_name}`, `{date}`, `{time}`, `{group_name}`, `{join_url}`, `{manage_url}`). Admins
   never edit Jinja or HTML, which rules out server-side template injection.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Channels, reminder rules, fixed layouts with text blocks | #2, #19 | built |
| 2 | Group texts and the editor (point 4) | #29 | built |
| 3 | Organisation override layer of the text blocks (point 4) | #168 | merged |

## Consequences

- `MailSender` and `SmsSender` in CLAUDE.md become one `MessageSender` port per channel kind.
- Work package 2 models channels, reminder rules and job planning. Work package 4 renders and
  sends messages. Work package 7 adds the editors.
- `group_text` replaces the `*_nl` / `*_en` columns (already decided in ADR 0004).
