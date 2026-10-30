# 0032. Provider-neutral contact-center integration

Status: proposed (2026-10-25). Renumbered from 0021 on 2026-10-27: two ADRs were given number
0021 in parallel; ADR 0021 is "Ending the work item of a cancelled booking". The content is
unchanged. Extends ADR 0007 and ADR 0018; once accepted, it replaces
their per-group mapping (ADR 0018 point 1) with the model below. Not built yet; see `docs/adr-status.md` for what is built.

## Context

OpenPlanner is going to be offered alongside more than one contact-center platform. Today,
Webex Contact Center (WxCC) shows up well past its adapter:

- `Group` has the fields `wxcc_business_address`, `wxcc_channel` and `wxcc_routing_value`.
  The domain validates them and the group settings form shows them.
- `Booking.wxcc_task_id`, the job type `dispatch_wxcc_task`, the work item type `WorkItem`
  (with a business address and a channel) and the webhook route `/webhooks/wxcc`.
- User-facing texts named "Webex Contact Center" in general explanations. As of this ADR,
  those general texts say "contact center" ("contactcenter"). The product name now appears
  only where a WxCC connection is configured.

The port `ContactCenterDispatcher` is already neutral in name, but its contract is WxCC's.
Adding a second platform (for example Genesys Cloud, Amazon Connect or Five9) would therefore
touch the domain, the schema, the jobs and the admin UI. ADR 0005 calls for one home per
concern, and the current layout breaks that rule.

The notification channels (ADR 0009) already solve the same kind of problem: a domain enum, one
port and one adapter per kind.

## Decision

1. **A contact-center provider is data.** The domain defines `ContactCenterKind`
   (`webex_cc` now; new values per added platform). The domain knows the kinds but not their
   APIs.
2. **One connection per organisation.** An organisation has at most one active contact-center
   connection: its kind plus credentials. It is configured on the integrations page and stored
   encrypted, write-only and audited (ADR 0008). The connection belongs to the organisation,
   not the installation, because different clients run different platforms.
3. **Per group, neutral routing.** A group gets one `ContactCenterRouting`:
   - `routing_value`: what the platform's flow or routing rules use to choose the queue.
     The default is the group slug.
   - `attributes`: provider-specific key/value settings (for WxCC, business address and
     channel).

   The routing is stored in its own table, `group_contact_center` (group, kind, routing value,
   attributes as validated JSON). This replaces the `wxcc_*` columns on `groups`.
4. **Each provider describes its own attributes.** Each adapter registers a
   `ProviderDescriptor`: kind, attribute names, maximum lengths, required flags and the i18n
   keys of their labels and hints. The domain validates `attributes` generically against that
   descriptor. The admin form is rendered from it, with no `if kind == ...` in routes or
   templates. Labels such as "WxCC business address" therefore stay correct for WxCC without
   appearing for other platforms.
5. **One neutral task, one dispatcher per kind.** `WorkItem` becomes `ContactCenterTask`.
   It carries only what CLAUDE.md allows (booking reference, first name, start label, group
   name, meeting link, kind `scheduled`/`direct`) plus the group's `ContactCenterRouting`. The
   application holds `dict[ContactCenterKind, ContactCenterDispatcher]`, as `MessageSender`
   does per `ChannelKind`. Each adapter maps the task to its own API (WxCC: Create Task v2,
   `workItemData`, `flowSettings.openplannerGroup`, per ADR 0018).
6. **Neutral lifecycle.** Inbound events are translated by the adapter into the domain's
   existing `EventKind` values (`task_created`, `task_connected`, `task_ended`, `task_failed`).
   - Webhooks move to `/webhooks/contact-center/{kind}`, each verified by its adapter's
     mechanism within 300 s. `/webhooks/wxcc` stays as an alias until the WxCC subscriptions
     are re-registered (runbook).
   - `Booking.wxcc_task_id` becomes `contact_center_task_id`, together with the kind that
     created it.
   - The job type `dispatch_wxcc_task` becomes `dispatch_contact_center_task`, and pending job
     rows are renamed in the migration.
7. **Texts.** General UI text, emails and docs say "contact center". A product name appears
   only in the descriptor-driven labels and on the connection's own configuration card.
8. **Privacy rule generalised.** CLAUDE.md's rule on outbound payloads now covers every
   contact-center provider, not only WxCC: payloads hold the first name and the host join URL
   only. Every adapter gets a test that asserts this on its recorded payload.

## Consequences

- One Alembic migration, reversible:
  - Upgrade: create `group_contact_center`, copy the `wxcc_*` values of every group into it
    with kind `webex_cc`, then rename the booking and job columns and values.
  - Downgrade: copy the values back.
- Once the generic `group_contact_center` table exists, adding a platform needs only its
  adapter, its descriptor, recorded contract fixtures and an ADR for that platform's API,
  authentication and webhook details. Domain, jobs and admin templates stay unchanged.
- The authorisation matrix is unchanged:
  - The organisation connection uses `integrations.manage`.
  - The group routing uses `group.edit`, as today.
- `docs/architecture.md`, `docs/dataflow.md`, `docs/webhooks.md` and `docs/threat-model.md`
  are updated in the same PR as the migration. The data-flow diagram then shows "contact-center
  provider", with WxCC as the current instance.
- Open point O6 (WxCC licence scope) stays specific to WxCC. A new open point is needed: which
  platforms are positioned first, and who owns each platform's flow configuration.
- Not in scope: making video meetings (ADR 0017, Webex) provider-neutral. The same pattern
  applies to them and gets its own ADR when a second video platform is needed.
