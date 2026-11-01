# 0028. Provider-neutral SMS gateway

Status: accepted (2026-10-26). Supersedes point 1 (the SMS half) and point 3 of ADR 0022 in
part: Webex Connect stays the only adapter, but it is no longer the definition of SMS.
Note (2026-11-01 review): where this ADR cites ADR 0021 for the provider-neutral contact
center, read ADR 0032 (renumbered on 2026-10-27); ADR 0032 is proposed, with nothing built yet.

## Context

ADR 0022 made SMS reminders go "through Webex Connect". That choice leaked past the adapter:
`SmsSettings` was documented as a Connect service, the secret was called `sms_service_key`,
and the admin portal said "SMS (Webex Connect)". SMS is a generic channel. Organisations
that do not run Webex, or that already have a contract with another SMS provider (CM.com,
Bird, Spryng, Twilio, …), should not need Connect to get SMS reminders.

ADR 0021 already solved the same problem for the contact center: the provider is data in
the domain, one port, one adapter per kind.

## Decision

1. **The SMS provider is data.** The domain defines `SmsProvider` (`webex_connect` now; one
   value per added gateway). `SmsSettings` carries the provider next to the fields every
   gateway has: API base URL (https only, validated as before), sender ID and country code.
   The secret is the provider's **API key** (`sms_api_key`; Connect calls it the service key).
   The domain knows the kinds, not their APIs.
2. **One adapter per provider.** `app/adapters/sms.py` holds what every gateway shares
   (`SmsGatewayConfig`, the GSM 7-bit check). `app/adapters/webex_connect_sms.py` stays the
   Connect adapter, unchanged on the wire. `SMS_GATEWAYS` in `app/adapters/senders.py` maps
   each provider to its adapter; a test fails when a provider has no adapter or no label.
3. **The admin portal asks for the provider.** The SMS card on
   `/admin/organisations/{org}/channels` is titled "Sms" and starts with a provider choice
   built from the enum; the texts name Webex Connect only as an example. Audit events name
   the changed fields (`sms.provider`, …) as before, never values.
4. **A stored secret stays with its host.** Saving with an empty key field keeps the stored
   secret only when its `secret_binding` is unchanged: provider and API host for SMS, server
   host for SMTP. Otherwise the form asks for the key again, so a key entered for one provider
   or host is never sent to another (a superuser's mistake or a hijacked session).
5. **Stored settings migrate.** Migration 0019 adds `"provider": "webex_connect"` to every
   stored SMS settings row. The downgrade removes the key and deletes rows of any other
   provider, which the old code would otherwise send through Connect.

## Consequences

- Adding a gateway is one enum value, one adapter module implementing `MessageSender`, one
  registry entry, one label in `nl` and `en`, a contract test with a recorded request, and a
  row in `docs/dataflow.md` for the new processor. No change to the domain services, jobs,
  routes or templates.
- A provider whose API needs more than an API base URL, key and sender (for example an account
  ID in the path) gets its extra fields then, in its own ADR, following the descriptor approach
  of ADR 0021 point 4.
- The form field and error key `sms_service_key` are renamed `sms_api_key`; nothing outside
  the admin portal used them.
- The data protection agreement must name the processor of the chosen provider; today that is
  still Cisco for every organisation.
