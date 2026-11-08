# 0032. Provider-neutral contact-center integration

Status: accepted for the organisation connection (2026-11-08, the product owner: "Contact center
per organisatie is inderdaad wat moet kunnen"; see *Amendment: the organisation connection*);
proposed (2026-10-25) for the rest until CC-0 names the next platform. Renumbered from 0021 on
2026-10-27: two ADRs were given number 0021 in parallel; ADR 0021 is "Ending the work item of a
cancelled booking". Extends ADR 0007 and ADR 0018; it replaces their per-group mapping (ADR
0018 point 1) with the model below. Partly built: steps CC-1 and CC-2 (domain, schema, port and
registry), CC-3 (webhook route per kind), CC-4a (the organisation's connection) and CC-5 (docs)
are merged, see *Build status* and `docs/adr-status.md`.
Note (2026-11-01 review): point 8 (first name and host join URL only) contradicts point 5 and
ADR 0018 §2, whose payload is what is sent (`app/adapters/wxcc.py`: booking reference, first
name, start label, group name, meeting link, kind). Point 8 amended by ADR 0081 §6. ADR 0043
was built without this ADR. ADR 0028 cites this ADR as ADR 0021 (its number before renumbering).

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

## Amendment: the organisation connection (2026-11-08)

The product owner decided point 2 and the two questions it left open: "Contact center per
organisatie is inderdaad wat moet kunnen".

9. **Everything of the connection belongs to the organisation.** An organisation's connection
   may be another tenant of the platform than the installation's or another organisation's. Per
   organisation it holds:
   - the kind and the platform's API host (https only);
   - the OAuth credentials: the client id, and write-only the client secret and the refresh
     token the Service App received when that organisation's administrator authorised it
     (ADR 0007, 0018);
   - a webhook secret that the server generates and rotates with a button, never typed and
     never shown (ADR 0042 §6, the inventory's "generated and rotated").

   The secrets are sealed with AES-256-GCM, write-only and audited (ADR 0008), in
   `integration_credentials` under the name `contact-center:{organisation id}`; the
   non-secret fields are a row of `organisation_contact_center`. The connection is edited under
   `integrations.manage` for that organisation, on its own page (ADR 0047: organisation →
   *Deelnemers* → *Kanalen*, next to its mail, SMS and alert bot), never on a platform page that
   asks "which organisation?".
10. **One webhook address per organisation.** The platform calls
    `/webhooks/contact-center/{kind}/{organisation}`. The address names the organisation by its
    slug, so its own secret verifies the event; a task is then looked up within that
    organisation's bookings only. A slug without a connection, or a kind it does not use,
    answers `404`.
11. **The installation's connection is the fallback for one minor** (ADR 0116). An organisation
    without a connection of its own keeps dispatching through the installation-wide settings
    (`WEBEX_CLIENT_*`, `WXCC_API_BASE_URL`, `WXCC_WEBHOOK_SECRET`) and its events keep arriving
    at `/webhooks/contact-center/{kind}` and `/webhooks/wxcc`. The minor after that drops the
    fallback, the installation-wide WxCC settings and the address without an organisation.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| CC-0 | The product owner accepts the ADR and names the first platform after WxCC (new open point) | – | planned (needs the product owner) |
| CC-1 | Domain and schema: `ContactCenterKind`, `ContactCenterRouting`, table `group_contact_center`, `bookings.contact_center_task_id`/`contact_center_kind`, job member `DISPATCH_CONTACT_CENTER_TASK`. Built as the expand step of ADR 0116, not as the single renaming migration of the *Consequences* below (ADR 0116 is the newer rule; its §5 keeps the previous image working on the new schema): migration 0098 copies the `wxcc_*` values forward; the application writes both the new and the old places, and for Webex Contact Center the old columns stay the source of truth on read. The stored job value stays `dispatch_wxcc_task` in this minor (`dispatch_contact_center_task` already reads); the next minor writes the new value, and the minor after that drops the old columns and the old job value | #616 | merged |
| CC-2 | Port and registry: `ContactCenterTask`, `ContactCenterDispatchers` (one dispatcher per kind), `ProviderDescriptor` per adapter with generic validation in the domain; the WxCC adapter maps the neutral task to Create Task v2 unchanged; recorded-payload test on the allow-list of CLAUDE.md (point 8 as amended by ADR 0081 §6) | #616 | merged |
| CC-3 | Webhooks at `/webhooks/contact-center/{kind}`, verified by the adapter within 300 s; `/webhooks/wxcc` kept as alias. Each adapter defines its `InboundWebhook` (check, parsing, secret), listed in `contact_centers.INBOUND`; a task is looked up with its platform. OpenPlanner still registers the WxCC subscription at `/webhooks/wxcc`; moving the registration comes with the organisation connection (CC-4) | #626 | merged |
| CC-4a | The organisation's connection (points 9–11): table `organisation_contact_center` (migration 0101), secrets sealed under `contact-center:{id}`, the card on organisation → *Kanalen* (`integrations.manage`, audited, write-only), the generated and rotatable webhook secret (ADR 0042 step 6b-2 for the organisation), and `/webhooks/contact-center/{kind}/{organisation}` verified with it | #PRNUM | merged |
| CC-4b | Dispatch, end and reconcile with the organisation's own credentials and API host, the installation's as fallback; rotating the secret re-registers the organisation's subscription | – | planned |
| CC-4c | The group form rendered from the adapter's `ProviderDescriptor` | – | planned |
| CC-5 | Docs: architecture, dataflow, webhooks, threat model, `scripts/diagrams/content.py`. Describes what CC-1 to CC-3 built (webhooks and threat model largely came with CC-3, #626); the organisation connection of CC-4 updates the same places when it lands | #627 | merged |
| CC-6 | Second platform adapter (for example Genesys Cloud) | – | planned (needs its own ADR) |

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
