# 0022. Message channels per organisation, SMS through Webex Connect, editable texts

Status: accepted (2026-10-25); superseded in part by ADR 0028 (SMS provider as data, Webex
Connect as one adapter). Implements ADR 0008 for SMTP and SMS, and the editors of ADR 0009.
Built; the organisation-wide text editor it deferred came with ADR 0074 (checked on 2026-10-31).

## Context

Until now only email existed: one SMTP server from the environment (`SMTP_HOST`) for the
whole installation, with the catalogue texts. HANDOVER asks for more: a sender per
organisation (§4), an SMS reminder (B6, §5 `send_reminder_sms`) through Webex Connect, which
is part of the WxCC subscription, and texts that admins can change (ADR 0009 point 4).
`ChannelKind.SMS`, `reminder_rules` and the `reminders.manage` / `texts.manage` permissions
already existed, but there was no SMS adapter, no screen for reminder rules and no editor.

## Decision

1. **Channel settings per organisation.** A superuser (`integrations.manage`, HANDOVER §6)
   configures each organisation's SMTP server (host, port, STARTTLS / TLS / none, username,
   sender address and name; "none" only without a login, so a password never travels in clear
   text) and its Webex Connect SMS service (API host, sender ID, country
   code) at `/admin/organisations/{org}/channels`. Non-secret fields are a JSON column on
   `channel_settings`; the SMTP password and the Connect service key are sealed with
   AES-256-GCM under `channel:<organisation>:<channel>` (ADR 0008), write-only in the UI and
   never read back into the domain. Saving without a key (`SETTINGS_ENCRYPTION_KEY`) is
   refused with a clear message. Every change is audited by field name.
2. **The environment SMTP stays as fallback.** An organisation without its own SMTP settings
   still uses `SMTP_HOST` (Mailpit in development). There is no SMS fallback.
3. **SMS through the Webex Connect SMS API v1.** `POST {host}/v1/sms/messages` with the
   service key in the `key` header; body `from`, `to` (E.164; national numbers get the
   organisation's country code), `content`, `contentType` (`text` for GSM 7-bit, else
   `unicode`) and a random `correlationId`. Status 201 is success; 429/5xx and network
   errors are retried, 4xx fail at once. Bodies are never logged. A Connect flow is not
   needed for sending. Delivery receipts (callbacks) are out of scope.
4. **The worker picks the sender per job.** For each message job it reads the group's
   organisation and the job's channel, builds the sender from the stored settings (fresh
   each time, so changes apply without a restart) and composes the message. A message whose
   channel has no sender fails at once as `channel_not_configured`, visibly in the job table;
   an SMS for a booking without a phone number is skipped.
5. **Reminder rules in the admin portal.** `reminders.manage` (organisation admins and the
   superuser) adds and removes rules per group: email or SMS, 5 minutes to 30 days before the
   start, at most five. SMS needs a group whose form asks for a phone number. The page warns
   when a rule's channel is not configured. New rules apply to new bookings; planned
   reminders of existing bookings stay, so nobody loses a reminder they were promised.
6. **Group texts.** `texts.manage` (group admins too, HANDOVER §6 "mail texts") edits, per
   group and locale, the subject and introduction of each email, the closing line and the
   SMS reminder text. The editor shows the text in effect; an empty field or the unchanged
   default stores nothing. Texts are validated in the domain: length, no control characters,
   no line breaks in subject lines (header injection) and only placeholders from the block's
   allow-list (`{date}`, `{time}`, `{group_name}` for email; plus `{first_name}` and
   `{manage_url}` for SMS). Lookup: group text → organisation override → catalogue. The
   layout, links and the calendar attachment stay fixed; admins never edit HTML or Jinja.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Channel settings, SMS through Webex Connect, reminder rules, group texts | #19, #29 | built |
| 2 | Organisation-wide text editor (the last Consequences point) | #168 | merged |

## Consequences

- New tables `channel_settings` and `group_texts` (migration 0013, reversible).
- Phone numbers now leave the system: to Webex Connect, only for SMS reminders of groups that
  ask for a phone number (`docs/dataflow.md`). The DPIA and the processor agreement with
  Cisco must cover SMS (B6).
- `JobRunner` takes a sender directory instead of one sender; `SmtpConfig` gained implicit TLS.
- An organisation-wide text override editor (the i18n override store, ADR 0004) is still to
  come; group texts cover the request for now.
