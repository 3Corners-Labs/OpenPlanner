# 0007. Contact-center dispatch through the WxCC Tasks API

Status: accepted (2026-10-23); points 1–2 superseded by ADR 0018. Supersedes HANDOVER B6 in part.

## Context

HANDOVER B6 assumed a dedicated Webex Connect tenant as the only bridge to Webex Contact
Center (WxCC), receiving a webhook at T−5 min and creating a task through a Connect flow. The
organisation has no dedicated Connect tenant: Webex Connect/Engage runs embedded inside WxCC.
The product owner expects the WxCC Tasks API (v2, work items) to be the way to create a task in
a queue.

## Decision

1. `ContactCenterDispatcher` (port in `app/domain/ports.py`) creates the task. Its adapter
   (work package 6) calls the WxCC Tasks API directly, authenticated with an OAuth token of a
   WxCC service app or integration. The exact endpoint version, payload and token lifetime are
   verified against developer.webex.com in WP6 and recorded in `docs/webhooks.md`.
2. Per group the mapping holds the WxCC queue ID. Further task attributes (task type or media
   type, title) are added to the mapping in WP6 once the API contract is verified.
3. The payload carries only what CLAUDE.md allows: first name, host join URL, queue ID, locale
   and the booking ID. No email, phone or full name.
4. Task outcome events (for open point O2, "not picked up") come from WxCC webhooks or event
   subscriptions. They are verified (signature or OAuth, per WxCC's mechanism) and rejected
   outside a 300 s window.
5. The SMS route is open. Either the Connect/Engage capability inside WxCC exposes sending, or
   an EU SMS gateway is used. Both sit behind the same `MessageSender` port (ADR 0009); the
   choice is made in WP6.

## Consequences

- The domain is unaffected: it plans a `dispatch_wxcc_task` job at T−5 min and stores the
  returned task ID. Only the adapter changes.
- The Connect-specific `X-Signature` webhook in CLAUDE.md is replaced by "verify every inbound
  webhook from WxCC; reject if |now − ts| > 300 s".
- Open point O6 (licence scope) now includes API access to Tasks for the service app.
