# 0082. Contact-center integration: verification, health, alerts to administrators and a fallback per group

Status: proposed (2026-10-31), requested by the product owner; partly built (step 1 in PR). Extends ADR 0018
(work items), ADR 0021 (ending a work item), ADR 0043 (own queue), ADR 0061 (alerts on the
overview, OV-6 and OV-7), ADR 0065 and ADR 0068 (group check, request events). Written on the
neutral port of ADR 0032, which stays as it is.

## Context

The work-item integration with Webex Contact Center (WxCC) runs: a booking gets a work item at
its lead time (ADR 0018), a cancelled booking ends it (ADR 0021), every call is a request event
(ADR 0068) and the group check walks the routing configuration (ADR 0065). The product owner
wants the next step to be the administrator's grip on that integration: tools to verify it,
to watch it, to be told when it breaks, and a safety net while it is broken. Today the gaps
are these:

1. **No round trip is ever proven.** The group check reads the WxCC configuration and creates
   a probe meeting at Webex, but it never creates a work item. `wxcc-check` sends an empty
   task that WxCC refuses with 400, which proves a scope, not the flow or the queue. The first
   real work item of a group is the first test of its flow, with a participant waiting.
2. **A task's state is known only through webhooks.** The port has create, end and
   mark-cancelled, no read. When WxCC sets a subscription to `inactive` after delivery
   errors, every task stays *queued* in OpenPlanner forever, and nothing says that the last
   event came in hours ago.
3. **Nothing watches the integration over time.** Request events are stored but not counted.
   *System status* shows the worker and the version, nothing about WxCC or Webex. A failing
   integration is visible only booking by booking, in the agenda or the audit log.
4. **No channel to administrators.** The alert model of ADR 0061 (OV-6, OV-7) is planned,
   not built; there is no mail or message to anyone when a job fails for good. The runbook
   expects someone to open the agenda.
5. **The fallback is manual.** ADR 0043 lets a group admin switch a group to the own queue
   during an outage. Nobody is told that the moment has come, and a booking whose dispatch
   failed for good at T-5 stays with the contact center on paper while the participant waits
   in the lobby.
6. **Two small gaps in the checks.** The worker area of the group check looks for
   `create_video_room` among the claimed job types but not for `dispatch_wxcc_task`; the
   automatic check (`integration_check_interval_hours`) runs only for groups with a video host.
7. **The set-up of WxCC lives in the runbook only** (sections 1 to 8 under *Webex meetings
   and Webex Contact Center*), written for the host team. The organisation's contact-center
   administrator has no step-by-step topic in the manual.

Constraints: no participant data in events, alerts or messages (CLAUDE.md); one home per
concern (ADR 0005, ADR 0047), so the alert model is the one of ADR 0061 and the fallback is
the own queue of ADR 0043, not a second mechanism; settings with a class (ADR 0042); a probe
may create a real work item (the product owner confirmed the configuration is on a test
tenant, and the flow gets a branch that ends a probe before an agent sees it); a fallback is
a choice of the group, never a surprise for its team.

## Decision

### Verification

1. **The port can read a task.** `ContactCenterDispatcher` gets `read_task(task_id)` returning
   the task's state (`queued`, `connected`, `ended`, `failed`, `cancelled`, `unknown`) and when
   it last changed; for WxCC through task search (`cjp:config_read`, already required). The
   adapter never returns the task's attributes or `workItemData`, only state and times.
2. **The group check creates a probe work item.** Next to the probe meeting of ADR 0065, the
   check creates a work item on the group's channel and business address with `kind: probe`,
   `bookingReference: probe-<uuid>`, `firstName: "Probe"`, the group's name, the probe
   meeting's link and the group's routing value, then waits up to
   `integration_probe_timeout_seconds` for `task:new` and `task:ended` on the webhook. That
   proves the token, the scopes, the asset, the flow, the subscription and the webhook path in
   one go. The flow ends a probe itself: the configuration check gets a rule **probe branch**
   (a warning when the flow has no Case on `kind == probe` that ends the contact: OpenPlanner
   then ends the probe itself with End Task, ADR 0021, and reports that as a warning too, so a
   tenant without the branch keeps a passing check). When the flow has the branch and
   `task:new` arrives but `task:ended` does not, the check fails with `probe_not_ended`.
   The probe is recorded as request events at the group's level, without a booking, like the
   probe meeting. A probe is never shown in the own queue, the agenda or the overview.
3. **The Service App probes move into the portal.** The probes of `wxcc-check` (token
   refresh, reads, empty task, empty message, End Task on a nil id, task search, region) run
   from a button *Service App controleren* / *Check the Service App* on *Koppelingen*
   (`integrations.manage`, fresh login, rate-limited like the group check) and show the same
   verdict table as the CLI, from the same code. The CLI stays for hosts without a portal
   session. Results are stored as an `integration_reports` row and audited, like the webhook
   commands.
4. **The checks cover dispatch.** The worker area fails with `worker.no_dispatch_jobs` when
   the group dispatches to a contact center and the worker does not claim
   `dispatch_wxcc_task`. The automatic check runs for every group with a contact-center
   mapping, whether or not it has a video host.

### Health

5. **The last inbound event per platform is recorded**: time, event type and outcome, for
   `/webhooks/webex` and `/webhooks/wxcc`, shown on *Koppelingen* and on the *System status*
   widget. WxCC events get the same `event_key` dedupe as Webex events (ADR 0010): a replayed
   event changes nothing and is logged once.
6. **A reconcile job keeps task states honest.** Every `task_reconcile_interval_minutes` the
   worker reads (point 1) every booking whose task is still `queued` or `connected`
   `task_reconcile_after_minutes` past its start, or whose last webhook is older than that,
   and whose booking is not yet purged. A difference is written on the timeline as the
   matching event with detail `reconciled` and updates the live status (ADR 0010). It writes a
   system event `integration.webhooks_missing` when a reconcile finds a state the webhook
   should have brought: that is how a silent `inactive` subscription becomes visible.
7. **Each connection has a health state**, computed from the `integration.request` events of
   the last `integration_health_window_minutes`, per platform (Webex, WxCC) and per
   organisation: `ok`, `degraded` (failures above `integration_degraded_failure_percent`),
   `down` (above `integration_down_failure_percent`, or the token refresh fails, or the last
   `integration_probe` failed), with a floor of three requests before a window counts, and
   `unknown` below it. Every transition writes a system event `integration.health` with the
   old and new state, the platform and the counts, at the organisation's level, including
   the return to `ok` after `integration_recovery_minutes` of healthy requests. The state is
   shown on *System status* and on *Koppelingen*; the overview's status widget turns it into
   a warning or outage alert (point 8). No new table: the state is derived from events and
   cached in `integration_reports`.

### Alerts to administrators

8. **One alert model, built as ADR 0061 OV-6 and OV-7 describe.** An alert is a system event
   at level warning or outage, in the user's reach; the banner, the pop-ups and the level
   filter are those of ADR 0061 point 7, and this ADR's step 3 updates that ADR's build table.
   The sources that this ADR adds: `integration.health` (degraded: warning; down: outage),
   `integration.webhooks_missing`, `integration.probe` failed, a job that failed for good
   (`dispatch_wxcc_task`, `create_video_room`, `end_wxcc_task`: warning), the worker's
   heartbeat stale (outage), and the fallback of point 11 switching on or off.
9. **Alerts are delivered outside the portal through alert channels.** A new port
   `AlertSender` in `app/domain/ports.py` with one adapter per `AlertChannelKind`:
   - `email`: through the existing email `MessageSender` (ADR 0009), fixed layout with i18n
     text blocks, in the organisation's default locale;
   - `webex_space`: a message in a Webex space through the Webex Messaging API
     (`POST /v1/messages`, markdown) with a **bot token** of a bot the organisation creates in
     the Webex developer portal and adds to the space. The bot is its own Webex app with the
     scope `spark:messages_write` only; the Service App of ADR 0017 is not reused, so the
     token that can create meetings and tasks never gains a messaging scope. The token is
     stored encrypted, write-only and audited (ADR 0008); the space is identified by its room
     id.

   Alert targets are configured per scope on the existing *Koppelingen* home: the superuser
   sets the platform's targets (platform events: updates, certificates, the worker), an
   organisation admin sets the organisation's (its integrations, its groups' jobs). Per
   target: channel, address or room id, minimum level. A button *Testmelding sturen* / *Send
   a test alert* sends one message to a target and records the outcome. A lasting condition is
   repeated every `alert_repeat_hours`, never more often; an alert of the same event key within
   that interval is counted, not sent. Delivery runs as a job with the usual retry; a failed
   delivery is a system event (never an alert of its own, to avoid a loop).
10. **An alert carries no participant data.** Level, source, platform, organisation, group,
    counts, reason code, the version and a link into the portal (the log entry or the booking
    page). Never a name, address, note, join link or token. A test asserts this on the
    rendered message of every channel.

### Fallback per group

11. **A group may choose a fallback.** The group setting `dispatch_fallback` has the values
    `none` (default) and `own_queue`. With `own_queue`, the group's bookings go to the own
    queue of ADR 0043 while the fallback is active:
    - **per booking**, when its dispatch job fails for good (after the retries of ADR 0068),
      that booking appears in the group's queue, flagged *Terugval* / *Fallback*, with the
      timeline event `fallback_queued`;
    - **for the group**, while the organisation's contact-center health is `down` (point 7):
      pending dispatch jobs skip with reason `fallback` and the bookings appear in the queue;
      the group returns to the contact center when the health has been `ok` for
      `integration_recovery_minutes`, and bookings not yet claimed in the queue are then
      dispatched after all. Both transitions are system events and alerts (point 8), audited
      with detail `fallback.on` / `fallback.off`.

    The `dispatch_kind` of the group does not change: the fallback is a state, visible as a
    banner on the group's *Wachtrij* tab, its settings and its agenda rows, and in the
    overview's queue widget. A group admin may end the fallback by hand (audited). Direct
    calls (ADR 0019) follow the same rule: with the fallback active they take the own-queue
    path of ADR 0072.
12. **The team is told, in its own space.** A group with `own_queue` may set a Webex space of
    its own (`fallback_space_room_id`, using the organisation's bot); without it the
    organisation's alert targets are used. The message on activation names the group and
    links to its queue; each booking that lands in the queue during the fallback sends one
    line with the start time, the first name and a link to the queue. **Never the host join
    link**: claiming in the queue gives it (ADR 0043 point 6), so two colleagues cannot start
    the same meeting unannounced, and the space keeps no link that outlives the booking.
    The first name is the one field CLAUDE.md allows outbound; the organisation chooses, by
    switching the fallback on, that its own space may receive it.
13. **The participant notices nothing.** The join link, the lobby, the trial call and the
    cancel and move rules stay as they are. What a participant is told when nobody picks up
    stays open point O2.

### Documentation and structure

14. **A step-by-step set-up in the manual.** A topic *Contactcenter inrichten* / *Setting up
    the contact center* in `docs/manual/{nl,en}/contact-center-setup.md`, written for the
    organisation's contact-center administrator, with one numbered step per screen: the
    Service App and its scopes, the Control Hub items of runbook section 3 (channel, asset and
    schema, entry point, flow with the `openplannerGroup` Case and the probe branch, queues),
    the group's fields in OpenPlanner, the webhook registration, then *Koppeling controleren*
    and what each line of its result means, and finally the bot for alerts (create the bot,
    add it to the space, paste its token on *Koppelingen*, send a test alert). The runbook
    keeps the host-side detail and links to the topic; the topic is shown behind the (i) of
    the group's contact-center fields and of the alert targets (ADR 0033).
15. **Admin structure (ADR 0047).** Nothing new at the top level. *Koppelingen* gains two
    sub-sections, *Controles* (Service App probes, last events, health) and *Meldingen* (alert
    targets, test alert). *System status* shows health and last events read-only. The group's
    *Settings* tab gains *Terugval* under *Hoe afspraken bij medewerkers komen*, next to the
    dispatch kind. The group's *Wachtrij* tab shows the fallback banner. No new tab.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `integration_probe_timeout_seconds` | C platform | 60 | 10–300 | `settings.platform.manage` | How long the group check waits for `task:new` and `task:ended` of the probe. |
| `task_reconcile_interval_minutes` | C platform | 5 | 1–60 | `settings.platform.manage` | How often the worker reads the state of stale tasks. |
| `task_reconcile_after_minutes` | C platform | 10 | 1–120 | `settings.platform.manage` | A task without a webhook this long after its start, or since its last event, is read. |
| `integration_health_window_minutes` | C platform | 15 | 5–120 | `settings.platform.manage` | The window of request events the health state is computed over. |
| `integration_degraded_failure_percent` | C platform | 25 | 1–100 | `settings.platform.manage` | Failure share above which a connection is `degraded`. |
| `integration_down_failure_percent` | C platform | 75 | 1–100 | `settings.platform.manage` | Failure share above which it is `down`; must be at least the degraded value. |
| `integration_recovery_minutes` | C platform | 10 | 1–120 | `settings.platform.manage` | Healthy this long before `ok` returns and a group fallback ends. |
| `alert_repeat_hours` | C platform | 4 | 1–168 | `settings.platform.manage` | A lasting alert is sent again at this interval; `tls_alert_repeat_hours` stays for certificates. |
| Alert targets of the platform (channel, address or room id, minimum level) | C platform | none | at most 10 targets | `settings.platform.manage` | Data on *Koppelingen*, listed because the superuser owns it. |
| Alert targets of an organisation, and its bot token | D organisation | none | at most 10 targets; token write-only, encrypted | `integrations.manage` | Data on the organisation's *Koppelingen*. |
| `dispatch_fallback` per group | D organisation (per group) | `none` | `none`, `own_queue` | `group.edit` | Explicit choice: the team must know the queue exists and work it. |
| `fallback_space_room_id` per group | D organisation (per group) | empty | a Webex room id | `group.edit` | Optional; the organisation's targets otherwise. |

Used, not new: `integration_check_interval_hours` (C, now for every group with a mapping),
`worker_heartbeat_max_age_seconds` (B), `job_overdue_minutes` (C), `tls_alert_repeat_hours`
(C), `not_picked_up_minutes` (D, per group).

**Fixed on purpose:** the probe's `kind` value and the Case branch name `probe` (the flow
and OpenPlanner must agree, so it is documented, not configurable); the floor of three
requests before a health window counts (below that a single failure would flip the state);
the content of alerts and fallback messages (a fixed layout with i18n text blocks, no editable
template, ADR 0009); that an alert never holds participant data and a space never gets the
host join link (privacy rules); that a failed alert delivery is never itself an alert; the
bot's single scope `spark:messages_write`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; row in `docs/adr-status.md` | #238 | in PR |
| 1 | Verification: `read_task` on the port and the WxCC adapter; probe work item and probe-branch rule in the group check and `wxcc-config-check`; Service App probes on *Koppelingen*; `worker.no_dispatch_jobs`; automatic check for every mapped group | #245 | in PR |
| 2 | Health: last inbound event per platform, WxCC event dedupe, reconcile job, health state with its events, *System status* and *Koppelingen* | | planned |
| 3 | Alerts: the alert model, banner and pop-ups of ADR 0061 OV-6/OV-7 (updates that ADR's table); `AlertSender` with the email and Webex-bot adapters; targets and test alert on *Koppelingen*; repeat and dedupe | | planned |
| 4 | Fallback per group: setting, per-booking and per-group activation, queue flag and banners, space messages, return to the contact center | | planned |
| 5 | Manual topic *Contactcenter inrichten* (nl, en) with the bot; runbook, `docs/threat-model.md`, `docs/dataflow.md`, `docs/webhooks.md`, `docs/architecture.md` | | planned |

## Consequences

- **Port and adapters.** `ContactCenterDispatcher.read_task`; a new port `AlertSender` and
  `AlertChannelKind` (`email`, `webex_space`); adapters `app/adapters/alerts_email.py` and
  `app/adapters/webex_messages.py` behind the existing `AuthorizedClient` so their requests
  are request events too. The bot token is a second credential next to the Service App, with
  its own card on *Koppelingen*. Fakes in `tests/fakes/`, recorded fixtures for the Messaging
  API and task search.
- **Jobs.** Two new job types, `reconcile_tasks` (periodic) and `send_alert` (one per
  delivery, with the usual retry); no new table for jobs. The health state and the last events
  live in `integration_reports`.
- **Schema.** One reversible migration: `dispatch_fallback` and `fallback_space_room_id` on
  `groups`, `fallback_active_since` on `groups`, `fallback` flag on `call_status`, and the
  `event_key` on inbound WxCC events. The bot token and alert targets reuse the encrypted
  settings storage of ADR 0008.
- **Authorisation.** No new permission: `integrations.manage` for probes, targets and the
  test alert; `settings.platform.manage` for the platform's targets; `group.edit` for the
  fallback; `queue.work` to see the flagged rows. The matrix test covers the new routes.
- **Threat model.** New rows: *probe flood* (the group check's rate limit covers the probe,
  and a probe creates at most one task per run); *alert flood* (dedupe and repeat interval;
  delivery failures are not alerts); *bot token leak* (write-only, one scope, rotation on
  *Koppelingen*, never in a job payload or log); *a crafted webhook that ends the fallback*
  (the fallback state is derived from request events OpenPlanner made itself, never from an
  inbound event). `docs/dataflow.md` gains the Webex Messaging API as a destination with the
  first name as its only participant field, under the fallback only.
- **Docs.** `docs/webhooks.md` documents the probe work item and the `reconciled` detail;
  `docs/audit-events.md` lists `integration.health`, `integration.webhooks_missing`,
  `integration.probe`, `fallback.on` and `fallback.off`; `docs/runbook.md` moves the set-up
  narrative to the manual topic and keeps host-side detail; `docs/architecture.md` shows the
  alert path and the fallback in the dispatch diagram; `docs/settings.md`,
  `docs/admin-map.md` and `docs/authorization.md` are regenerated.
- **Not decided here:** alerts through SMS or WhatsApp (the port allows a third kind by ADR);
  WxCC live statistics for the direct-call cap (ADR 0026 deferred them, and `read_task`
  does not change that); what the participant is told during a fallback (O2); and the
  provider-neutral tables of ADR 0032, which, once built, carry `read_task` and the probe
  without a change to this ADR.
