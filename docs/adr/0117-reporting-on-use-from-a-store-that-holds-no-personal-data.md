# 0117. Reporting on use, from a store that holds no personal data

Status: accepted (2026-11-06, PR #558), requested by the product owner (open point O16). Partly built:
steps 1 (the domain), 2 (the store and the booking facts), 3 (the call, queue, message, rebooking and
slot facts), 4 (rollup, retention, backfill, lag) and 5 (permission, navigation, query layer, the
*Volume* screen) are merged; the rest is planned.
Extends
ADR 0102 (what remains after a purge; step 4 is its first statistics table), ADR 0014
(authorisation), ADR 0023 (machine API), ADR 0047 (admin structure), ADR 0042 (settings) and
ADR 0009 (messages). Relates to ADR 0019, 0024, 0043, 0044, 0056, 0057, 0069, 0072, 0081 and 0085.

## Context

Organisations want to know how their planner is used: how many participants book, for which
subject, how long they wait, how long a call lasts, who does not show up, how full the agenda is,
which hours are busy, how much each team member handles, and whether the mails arrive. O16
(2026-11-05) asks for a screen in the portal, a CSV export and the events in the customer's SIEM
or BI environment, with no personal data. A data analyst wants more than counters: every cut by
organisation, group and subject, by queue method, by hour and weekday, with durations as
distributions rather than one average.

Three facts of the current code decide the shape.

1. **The source rows disappear.** `purge_pii` empties the personal fields 24 hours after the slot
   (ADR 0102); `BookingRowRollup` deletes the booking row, its `booking_events` and its
   `call_status` `booking_row_retention_days` (730) after the purge and keeps only
   `booking_day_counts` (group, UTC day, final status, count). Reporting that reads bookings,
   events or call status therefore sees at most the last 24 hours plus the retention term, and
   never what the durations were. Reporting must be fed when things happen, not when someone asks.
2. **Times are identifying.** A row with exact start, group and duration points at a person
   together with data held elsewhere; that is why ADR 0102 deletes the row. A statistics store
   must not rebuild the row under another name. It holds counts and sums per hour and per
   cut, never one line per booking.
3. **SQLite has one writer.** The web path takes the write lock for every booking
   (`BEGIN IMMEDIATE`). A report that scans bookings and events on the web path would compete with
   participants. Reports must read a small, pre-aggregated table that the worker builds.

The decision-tree, the booking page and the widget never log a participant in, so a report is
read only by staff and by API clients; there is no participant-facing statistics.

## Decision

1. **A separate store without personal data, fed by facts.** A *fact* is one small record
   written in the same transaction as the state change it describes (a booking is created,
   confirmed, cancelled, moved, completed; a call is connected or ended; a message is sent). Where
   the change is made by a job or a webhook, the fact is written in that job's or handler's own
   transaction. The insert runs in a savepoint: if it fails, the state change still commits, the
   fact is counted as dropped (shown on *System status* and logged without content) and never
   fails a participant's action. With `reporting_enabled` off nothing is written. The usage queries get their own Protocol in `app/domain/ports.py`, not more methods on the already large `Transaction`; only `record_usage` joins the transaction.
   Facts are written through one method of the `Transaction` Protocol (`record_usage`), built
   by a pure function in `app/domain/usage.py`. Nothing in the reporting path reads a purged or
   purgeable column: the facts are made from values the service already holds at that moment, and
   a test fails if a fact field is not in the allow-list of point 2.
2. **What a fact may hold (allow-list, fixed).** `bucket_hour` (the UTC hour of the instant the metric is
   keyed on, point 3), organisation, group, fact kind, and these codes: booking kind (`scheduled` or `direct`), dispatch kind (contact center,
   own queue, calendar invite), subject code (ADR 0081, empty until built), locale, channel kind
   (email, sms), outcome, actor class (`participant`, `planner`, `closure`, `system`), reason
   code, and numbers (`value_seconds`, `participants`, `capacity`, `booked`). No fact holds an exact
   time: the builder truncates to the hour, and the autoincrement id (not a timestamp) is the rollup
   watermark. Never: name, email,
   phone, form answers, any join URL, booking id or reference, management token, free text,
   or the pseudonym of an agent. `staff_id` is not in the general fact: it exists only in a separate
   typed `StaffFact` of point 9, and `USAGE_FIELDS` lists both shapes. `app/domain/privacy.py` gets a fourth list, `USAGE_FIELDS`, and a
   test checks that no field of `DATA_FIELDS` or `DERIVED_FIELDS` is in it (the mirror of the test
   that `purge_personal_data` works from the lists). Written as ADR 0102 asks, "deleted", never "anonymised":
   the statistics hold no personal data; they are not a copy of the appointments with the names
   taken out.
3. **Two stores, one short and one long.**
   - `usage_events`: the facts as written, an outbox and the source for a rebuild. The worker rolls them
     up and deletes them after `reporting_event_retention_days`. It is the one place that holds one row
     per event (hour-truncated time, exact `value_seconds`); it is listed as such in the retention
     matrix of `docs/dataflow.md` with the reason for its term, and a row has no booking id, so it cannot
     be joined back to an appointment.
   - `usage_hour`: the long-lived rollup. One row per (`bucket_hour`, organisation, group, metric, typed nullable cut columns
     `booking_kind`, `dispatch_kind`, `subject_code`, `locale`, `channel_kind`, `outcome`, `actor_class`,
     `reason_code`, duration `bucket`) with `count`, `sum_value` and `max_value`; the domain type is a
     frozen dataclass `UsageCell`, and no JSON or string key crosses the adapter boundary. **What an hour
     means is declared once:** the metric table in `app/domain/usage.py` states for every metric the
     instant its hour comes from (creation, slot start, or the event itself) and how it aggregates
     (count, sum, or max, which Q9 needs). Outcome and capacity facts are therefore keyed on the slot
     start, which is exactly the cohort of point 6, so no query holds its own cohort rule. The hour is the finest grain; a
     weekday or local hour is derived at render time from the group's timezone (CLAUDE.md: time
     conversion only at the edges, which also keeps daylight-saving changes right). Durations are
     kept as counts per fixed bucket plus an exact sum, so a median or a 90th percentile is an
     estimate within a bucket and the mean is exact. Rows older than `reporting_retention_months`
     are deleted by the worker's daily housekeeping.
   - `usage_staff_hour`: the same for load per team member, keyed by `staff_id` (point 9).
   `booking_day_counts` stays as it is (point 14).
4. **Who writes, who reads, on which path.** The web path only inserts facts (one small row per
   transition, inside the existing transaction). The worker's `usage_rollup` job reads `usage_events`
   after a stored watermark, upserts `usage_hour` in batches (a fixed batch bound, like
   `booking_rollup.BATCH_SIZE`, short `BEGIN IMMEDIATE` per batch) and advances the watermark in the same
   transaction, so a crash neither double-counts nor loses a fact (idempotent, tested). Report pages,
   exports and the API read only `usage_hour` through indexed, scope-filtered queries with a
   mandatory date range and a row cap. A report shows *as of* (the watermark time) so a reader
   knows how fresh it is; the *System status* page shows the rollup lag and warns past a threshold.
5. **Backfill, honestly.** `python -m app.cli usage-backfill` (and an automatic run after the
   migration) builds facts from what still exists: bookings, `booking_events` and `call_status`
   not yet rolled up, which is the last 24 hours plus up to `booking_row_retention_days`. For
   earlier days only `booking_day_counts` exists, and it stays the place for them: the *Volume*
   screen shows those outcome counts per day as a separate row *before reporting*, never as fake hours
   in `usage_hour`, so there is no `coarse` mode in the shared table. Durations, queue method and lead
   time start on the day the release is installed. Backfill is idempotent: a booking already turned into
   facts is recorded in its own watermark row and not done twice.
6. **Metric catalogue.** The definitions below are the contract of the screens, the CSV and the API;
   `docs/reporting.md` is generated from the same table in `app/domain/usage.py` and CI fails if they
   differ. A *cohort* is the bookings whose slot starts in the period (group-local day); a rate
   counts only bookings whose outcome is final, and the most recent 24 hours are marked
   *provisional*. A booking moved to another slot counts once, in the cohort of its final slot;
   the move itself is a separate fact (`booking.created` carries `is_move`, so *created* never
   includes moves).

   *Volume and outcome*

   | ID | Metric | Exact definition | Fact(s) |
   |---|---|---|---|
   | U1 | Bookings created | Count of `booking.created` with `is_move` false, by creation hour | `booking.created` |
   | U2 | Outcomes | Count per final outcome: completed, no show, cancelled by participant, by planner, by closure, by system, expired unconfirmed | `booking.outcome`, `booking.cancelled`, `booking.confirm_expired` |
   | U3 | No-show rate | `no_show / (completed + no_show)` in the cohort; cancelled and suspended excluded | `booking.outcome` |
   | U4 | Cancellation rate | cancelled by participant `/` (created in the cohort, excluding moves) | `booking.cancelled`, `booking.created` |
   | U5 | Late cancellation | Share of cancellations whose lead time is inside the group's `participant_change_lead_minutes` (ADR 0069) | `booking.cancelled.lead_seconds` |
   | U6 | Lead time | `slot.start − created_at`, in the buckets of point 7, per created booking | `booking.created.value_seconds` |
   | U7 | Reschedules | Count of `booking.moved` by actor class and reason; rate `moved / bookings in the cohort` | `booking.moved` |
   | U8 | Rebooking funnel (ADR 0044, 0057) | proposed, accepted, expired; conversion `accepted / proposed`; time to accept in buckets; split by planner-asked and assistant-proposed | `rebook.proposed`, `.accepted`, `.expired` |
   | U9 | Confirmation conversion (ADR 0056) | `confirmed / (confirmed + expired unconfirmed)`; time to confirm in buckets | `booking.confirmed`, `booking.confirm_expired` |
   | U10 | Closure impact (ADR 0024) | Bookings suspended, resumed, cancelled per closure; no closure id is stored, only the day and counts | `booking.suspended`, `.resumed` |
   | U11 | Participants per call | Sum and mean of `participants` of finished calls | `call.ended` |

   *Queue method and time*

   | ID | Metric | Exact definition | Fact(s) |
   |---|---|---|---|
   | Q1 | Queue-method mix | Share of bookings by dispatch kind (contact center, own queue of ADR 0043, calendar invite) and booking kind (scheduled, direct per ADR 0019 and 0072 including *start now*) | `booking.created` |
   | Q2 | Task wait | `connected_at − task_created_at` of a call dispatched to the contact center or the own queue; buckets, exact mean | `call.connected` |
   | Q3 | Host punctuality | `host_joined_at − slot.start`, signed (negative: the host was early); only scheduled calls | `call.host_joined` |
   | Q4 | Participant wait in the meeting | `host_joined_at − max(slot.start, guest_joined_at)` when positive | `call.host_joined` |
   | Q5 | Call length | `ended_at − started_at` where both are set; buckets, exact mean | `call.ended` |
   | Q6 | Abandoned direct calls | `waiting_left / direct calls created`, with the wait at the moment of leaving in buckets; and the count not picked up within `not_picked_up_minutes` | `call.waiting_left`, `call.not_picked_up` |
   | Q7 | Own-queue claim | Share claimed, time from the queue becoming visible to `claimed`, releases per claim | `queue.claimed`, `queue.released` |
   | Q8 | Dispatch health | Count of `task_failed`, `meeting_failed` and `integration_error` per day, by a code from a closed list (not the provider's text) | `call.failed` |
   | Q9 | Direct-call load | Peak concurrent waiting-plus-connected per group and hour (ADR 0072 cap), computed from facts, not from live state | `call.created`, `.connected`, `.ended` |

   *Capacity and load*

   | ID | Metric | Exact definition | Fact(s) |
   |---|---|---|---|
   | C1 | Utilisation | `sum(booked) / sum(capacity_snapshot)` over slots that have started; `booked` = confirmed + completed + no show, not cancelled and not pending | `slot.closed` |
   | C2 | Fill distribution | Slots by fill: empty, under half, under full, full | `slot.closed` |
   | C3 | Idle capacity | `sum(capacity − booked)` over started slots | `slot.closed` |
   | C4 | Busiest hours and days | Bookings by group-local weekday and hour of the slot start, as a table with a heat shade and the number in each cell | `booking.created` (slot hour as a code), derived at render |
   | C5 | Shortfall | Blocks where staffed agents were fewer than bookings (ADR 0044), split accepted and resolved | `slot.shortfall` |
   | S1 | Load per team member (point 9) | Calls handled, call minutes, claims, on-time share per `staff_id` | `call.ended`, `queue.claimed` |
   | S2 | Team utilisation | `handled call seconds / staffed seconds` of the blocks in the group's staffing plan | `call.ended`, `slot.closed` |

   *Channels, locales, trial calls*

   | ID | Metric | Exact definition | Fact(s) |
   |---|---|---|---|
   | M1 | Messages | Sent and failed per channel kind (email, sms) and purpose (confirmation, reminder, change, cancellation); failure rate | `message.sent`, `message.failed` |
   | M2 | Locale mix | Bookings by locale | `booking.created` |
   | T1 | Trial calls (ADR 0071, 0076) | Share of bookings with a trial created; of those, followed the link; of those, entered the meeting (the gap is people stuck in the lobby); failures | `trial.created`, `.joined`, `.entered`, `.failed` |

7. **Fixed duration buckets.** Seconds are counted in the buckets 0–1 min, 1–5, 5–15, 15–30, 30–60,
   1–2 h, 2–6 h, 6–24 h, 1–7 d, over 7 d (the same set for every duration; signed durations use a
   mirrored set around zero). The bounds are constants on purpose: changing them would make history
   incomparable. A change needs a new metric version.
8. **Privacy: small cells.** The store holds exact counts; every read (screen, CSV, API, digest)
   goes through one function `suppress()` in the domain.
   - A cell with a count below `reporting_min_cell_size` (C, default 5, bounds 3–20) shows `< 5` and its
     sum, mean and buckets are blanked.
   - **Complementary suppression.** When exactly one cell of a row or column that has a stated total
     is suppressed, the next smallest cell is suppressed too, so the total cannot be subtracted back.
     A total is shown only if it is itself at least the threshold.
   - An organisation may raise the threshold for its own view (D `reporting_org_min_cell_size`, from
     the platform value up to 50), never lower it.
   - **Fixed cuts only.** There is no free pivot: a report is a defined set of dimensions (organisation,
     group, subject, queue method, locale, weekday, hour), at most two cut dimensions at a time and
     one time grain. Free cross-tabulation is how small cells are rebuilt; the analyst who needs more
     uses the API, which has the same suppression and the same cuts.
   - Time ranges snap to whole group-local days. Hour-level cuts (weekday × hour) are offered only over
     a range of at least 7 days, and a cut (subject, locale, hour) is offered for a group only when
     the group's total in the range is at least five times the threshold; below that the group shows
     its total, or nothing. This is what stops two reports over adjacent ranges being subtracted into
     one hour of one booking (R2).
   - Locale and subject cuts of a small group are subject to the same rule; they are the usual path
     to a person ("the only English-speaking booking on Tuesday at 10:00").
   The platform scope (superuser) shows sums across organisations; an organisation never sees another
   organisation's figures, not even in a total (the query takes the `Scope`, point 11).
9. **Team members are people too.** Load per team member (S1, S2) is personal data of the staff, with
   its own interest and, in many organisations, works-council rules. It is a separate table
   (`usage_staff_hour`) keyed by `staff_id` of the `staff` row (never the agent pseudonym of
   `claimed_by`, which is a purged field of the booking), switched off by default
   (`reporting_staff_view_enabled`, D, default off), shown only with its own permission to the
   organisation admin only (not the group manager), under the same small-cell rule (a staff member
   with fewer than the threshold calls in the range shows `< 5`), and with no ranking order: the table
   is sorted by name and the screen has no *top* or *worst* list. When a staff member is removed the
   worker folds their rows into one *former team members* row (`staff_id` null), so history keeps
   its totals and no individual remains; the same fold applies to staff facts still in the outbox.
   While `reporting_staff_view_enabled` is off no staff fact is written at all. Staff metrics are
   never in `usage.fact` log events (point 15); the tidy CSV and the API include them only for a
   caller with `reports.staff.view` (an API client needs the separate client right `reports:staff`,
   off by default).
10. **Dashboards, server-rendered, no JavaScript framework (ADR 0016).** Pure HTML and inline SVG
    produced by Jinja macros; geometry comes from one helper in `app/web/charts.py` (shared plumbing
    under `app.web`, the one home for chart drawing). Rules, each tested with axe and a
    keyboard-only run:
    - Every chart is a `<figure>` with a `<figcaption>`, an accessible name, and the **same numbers in a
      `<table>` directly after it** (always present, not hidden), so the chart is never the only
      carrier (WCAG 1.1.1, 1.4.1).
    - Series differ by label and pattern as well as by colour; colours come from the existing design
      tokens, with contrast checked at 3:1 for graphics and 4.5:1 for text (1.4.3, 1.4.11); no
      inline style attribute, only classes (CSP, ADR 0016).
    - Axes, units and *as of* are text. The busiest-hours view is a table with a shade and the number in
      every cell, so shade is never the only signal.
    - The **baseline** needs no script: static HTML and SVG from the rollup, refreshed by reload. Points 19
      and 20 add interactivity and a live wallboard as enhancements on top of that baseline, never instead of it.
    - Print stylesheet and an `@media print` table-only fallback; reduced motion has nothing to
      reduce because nothing moves.
11. **Where it lives (ADR 0047).** Placement is decided here because a new top-level entry needs an
    ADR (ADR 0047 §5, §7).
    - **Side navigation, section Monitor:** one new entry **Reports** (`monitor.reports`, `/admin/reports`,
      wide, permission `reports.view`). It is the cross-object list and the platform scope: a table of
      organisations (superuser) or of the groups in the user's scope with a few headline figures, each
      row linking to the object's own tab. It has no edit forms (ADR 0047: cross-object pages are
      lists). Monitor goes from three entries to four; the sections stay at three.
    - **Organisation page and group page:** one sub-tab **Usage** under the existing *Overview* tab (the
      register has two levels, tab and sub-tab, so there is no third). The report family is a closed
      `view` query value (`volume`, `queue`, `capacity`, `channels`, `team`) as the audit screen uses its
      filter, with a link row on the page. No new top-level tab: the organisation stays at six tabs, and the
      group, which has ten until ADR 0047 build step 4, gets none. The check against "a seventh tab needs
      an ADR": none is added.
    - **Exports and the digest subscriptions** are buttons and a small form on those pages; the
      subscription list is a section of the organisation's Usage page (one home, never a second page that
      asks "which organisation?").
    - Permissions (matrix in `app/domain/authorization.py`, ADR 0014, docs generated): `reports.view`
      (level group), `reports.export` (level group), `reports.staff.view` (level organisation),
      `reports.digest.manage` (level organisation). Proposed holders, to confirm at build step 5: view and
      export for superuser, organisation admin, group manager and auditor; view for planner;
      `reports.staff.view` for the organisation admin only; digest management for the organisation admin.
      Every report query takes the `Scope` derived from the permission and the SQL adapter filters by it;
      every decision is logged, denials and exports go to `audit_events`. Superuser is a scope that matches
      everything, not a bypass. The matrix sweep (every role × every report endpoint × own and other
      scope, including CSV, API and digest recipient resolution) is part of step 5 and runs on `ci:full`.
12. **Export.**
    - **CSV on every report page:** exactly the table on screen (after suppression and the user's
      filter), through the code that already exists in `app/domain/audit_export.py` (`ExportLimits`,
      `export_window`, `safe_cell`, `to_csv`), which step 8 moves unchanged to a neutral
      `app/domain/csv_export.py` so both exports share one implementation and one escaping (UTF-8 with BOM, `;`, RFC 4180, CRLF, a leading
      `'` against formula injection). Limits `reporting_export_max_rows` and `reporting_export_max_days`;
      an export over a limit is refused with a code, never cut short silently. The file ends with no
      checksum line; the response carries `X-Content-SHA256`, and the audit event stores it.
    - **Tidy CSV `openplanner.usage/1`** for analysts: one row per `usage_hour` cell (hour, organisation,
      group, metric, cut codes, bucket, count, sum) after suppression, with a documented column list
      and a version; the version changes only with an ADR amendment.
    - **Audit:** every export writes `reports.export` with the permission, scope, filter (codes only), row
      count, checksum and who; no content in the event.
    - **Not offered:** Parquet and OData (a heavy dependency for a SQLite single-writer product, with a
      protocol surface the threat model would have to cover), and a read-only database view for BI
      tools (it would bypass scope filtering, suppression and audit, and the file is held by the web
      writer). A BI tool uses the API or the CSV; a customer who wants events in their SIEM uses point 15.
13. **Machine API (ADR 0023 style).** `GET /api/v1/reports/usage` with an API client token of scope
    `reports:read`, limited to the groups the client is bound to (ADR 0023, `api_client_groups`); query
    parameters: `metric`, `from`, `to` (whole days), `group`, `cut` (from the fixed list), `format`
    (`json`, `csv`, `ndjson`). Pagination is a cursor over the sorted cells, `as_of` and `ETag` in the response so a
    client can poll cheaply, `Cache-Control: private`. The page size is `reporting_api_page_size`; the
    rate limit comes from the common limiter (CLAUDE.md: one central place), with
    `reporting_api_rate_per_minute`. NDJSON streams a page at a time from `usage_hour`, never holding the
    write lock. Suppression, the cut list and scope are the same code as the screens. The reference
    goes in `docs/reporting-api.md` (generated metric table plus examples), the OpenAPI spec is
    diffed, and schemathesis runs against it. A new API client permission is set in the same place as
    other client rights and is audited when granted.
14. **Existing table stays.** `booking_day_counts` and `BookingRowRollup` (ADR 0102 step 4) are not
    changed: they remain the retention overview's source and the only record of days before this ADR.
    The new rollup reads facts, never `booking_day_counts`, and `BookingRowRollup` does not write
    `usage_hour`, so there is one writer per table and no dual write. A later ADR may retire the old
    table once its oldest rows age out (about two years) and the *before reporting* row is empty.
15. **Events to the customer's log platform (O16).** With `reporting_usage_events_to_log` (C, default
    off) every non-staff fact is also emitted as one structured system event `usage.fact` through the log
    destinations of ADR 0085, after commit, with the allow-listed fields only, the hour-truncated time
    and the duration bucket instead of exact seconds (the customer's retention there is theirs). They hold no personal
    data, so the PII masking processor has nothing to mask, and a test asserts it on a fake stream. This
    gives a SIEM or BI team the raw facts at the grain they choose, with their own retention, and costs
    nothing on the web path.
16. **Digest mail.**
    - A *subscription* belongs to an organisation or one group: period (daily, weekly on a chosen weekday,
      monthly on a chosen day), send hour (D `reporting_digest_send_hour`, in the organisation's timezone),
      and recipients chosen from **staff members** who hold `reports.view` for that scope at the moment of
      sending. There are no free addresses: a mail to a typed address is an exit for data and an
      unaudited share. A staff member who loses the permission is skipped and the subscription says so on
      its list.
    - **The mail carries a link, not a file.** Text and layout are the fixed templates of ADR 0009 with
      text blocks from `app/i18n` (keys `reports.digest.*`, `nl` and `en`, organisation override, never
      HTML), `{placeholder}`s only from the allow-list `{organisation}`, `{group}`, `{period}`,
      `{bookings}`, `{no_show_rate}`, `{average_wait}`, `{utilisation}`, `{link}`. The figures are the
      suppressed ones; with `reporting_digest_include_numbers` off (D, default on) only the link remains.
      The link goes to the authenticated report page of that period and carries no token and no personal
      data (the user logs in as usual; the login return target resolves only through the navigation
      register). No CSV attachment: a file in a mailbox outlives the portal's
      permission checks. Plain-text part always, semantic HTML, text alternative for the logo (CLAUDE.md).
    - A job `reports.digest` in `app/jobs/` runs hourly from the worker; one run record
      `(subscription_id, period_start)` is unique, so a retry or a second worker never sends twice. A window
      missed because the worker was down is sent late within `reporting_digest_late_hours`, otherwise
      skipped and noted on the subscription. Messages go through the `MessageSender` port (email adapter);
      the recipient's address is read at send time and never copied into the job payload (no PII in job
      payloads). Language: the staff member's preference, else the organisation default, else `nl`.
    - A subscription is created, changed or deleted only with `reports.digest.manage`, and each change is
      an audit event.
17. **Migrations and rollback (ADR 0116).** One expand-only migration in minor N creates `usage_events`,
    `usage_hour`, `usage_staff_hour`, `report_subscriptions` and `report_digest_runs`, with indexes on
    (organisation, group, hour). Nothing existing is altered, so the previous minor can run on the same
    database; the rollup watermark lives in its own row. Reversible: the downgrade drops the five tables.
    A patch release carries none of this.
18. **Documentation and manual.** Manual topics `docs/manual/nl/reports.md` and `docs/manual/en/reports.md`
    (one topic per screen: Volume, Queue and calls, Capacity, Channels, Team, Digest, Export), the (i) on
    each metric names its ID and definition from the catalogue; `docs/dataflow.md` gets the new store and the
    fact flow and the retention matrix gets the three tables; `docs/threat-model.md` gets the abuse cases
    below, each with a test; `docs/reporting.md` and `docs/reporting-api.md` as in points 6 and 13.

19. **Interactive charts as progressive enhancement.** One small vanilla script,
    `app/web/static/report-charts.js` (no library, no build step beyond the repo's own, no framework: the
    stack rule stays), loaded with the script CSP on report pages only, as `live-region.js` is (ADR 0072
    §6). It adds to the server-rendered SVG: hover and keyboard focus on every data point with a tooltip
    (also reachable with arrow keys, text from the table beside the chart, set with `textContent`), series
    toggles as real buttons with `aria-pressed`, a crosshair, *download as SVG/PNG*, and drill-down that is
    an ordinary link to the same page with a narrower filter (server rendered, suppression applied). Ranges
    and cuts stay GET forms and links, so every state has a URL and works without the script. Rules: the
    page is complete and correct with JavaScript off (a Playwright run with scripts disabled for every
    report page and locale); axe with scripts on; no chart gets its data from anywhere but the page. A
    charting library (canvas or WebGL output is not readable by assistive technology, needs a dependency
    review and in some cases a looser CSP) and embedding a BI product (it would bypass scope, suppression
    and audit) are refused here; a customer who wants a full BI front end feeds it from the API of point 13,
    and `docs/reporting-api.md` carries a worked example.
20. **Wallboard.** A live display of one group's or organisation's day, for a screen in the team's room.
    - **Home (ADR 0047):** not a new entry: it is the report family `view=wallboard` of the *Usage* page, with
      a full-screen layout variant (`layout=kiosk`, no menu) that goes through `app.web.templates.render`.
    - **Content:** tiles of large type with a text label each: waiting now, connected now, longest wait now,
      calls today, no-shows so far today, bookings per hour today as a bar strip, utilisation of the current
      and the next blocks, dispatch failures in the last hour. Counts only; no name, reference or link.
    - **Live data:** the tiles read the same bounded, indexed live state as the queue screen (the current
      blocks only, no scan) and today's earlier hours from `usage_hour`. They poll the shared fragment mechanism
      of ADR 0072 §6 with `live-region.js` every `reporting_wallboard_refresh_seconds`; no WebSocket, no
      server-sent events, no second polling mechanism, no write on the poll path.
    - **Honesty about age:** every tile states *as of hh:mm:ss*; after three failed polls the whole board says
      in text and colour that its data is old since that time and stops showing numbers as current. A board
      that silently freezes is worse than none.
    - **Alerts:** a wait over `reporting_wallboard_wait_alert_minutes` shows the tile with the words *long wait*
      (not colour alone) and is announced once through the polite live region; no sound, no flashing, and
      `prefers-reduced-motion` and `prefers-contrast` are honoured. Text sizes scale with the screen and meet
      4.5:1 at the sizes used.
    - **Suppression:** historic and ratio tiles go through `suppress()`. The live counts (waiting, connected,
      longest wait) are operational state without identity, shown exactly, and only to a viewer who may
      also see the queue (`queue.view`) in that scope.
    - **Who can open it:** a signed-in staff member with `reports.wallboard.view` (level group) and the scope.
      For a screen that nobody sits at, an organisation admin may create a **display link** per group or
      organisation (D `reporting_wallboard_display_links_enabled`, default off): a random 32-byte token
      shown once, stored only as `sha256(token)`, expiring after at most `reporting_wallboard_link_max_days`,
      revocable, listed with its last use, every creation, use of a new device and revocation an audit
      event (permission `reports.wallboard.link.manage`, level organisation). The link is **one-time**: the first
      request consumes it (compared with `hmac.compare_digest`, rate limited by the common limiter) and
      a second use is refused, so a link that leaks later is worthless; a replaced screen needs a new link. The
      first request sets a read-only kiosk session cookie (`Secure; HttpOnly; SameSite=Lax`) and redirects to the same page
      without the token in the URL, so the token is not left in history or a referrer, and the log processor masks that path segment (the
      first request is the one place a token is in a URL; the proxy's access log is documented in the
      runbook to mask it too); the cookie opens
      the wallboard of that scope only, no other admin route, and never the *Team* view. The kiosk session lives as long as its link would
      have (never longer than `reporting_wallboard_link_max_days`), dies with a revocation within one poll,
      and two optional address checks keep a stolen cookie useless elsewhere, both applied at the exchange
      and again on **every request that carries the cookie** (every poll), not only at the start:
      (1) an **ACL** of CIDR ranges, a default per organisation (D `reporting_wallboard_link_networks`,
      empty means any) that a link may narrow when it is created; (2) a **pin**: with *pin to the first
      address* chosen on the link, the address seen at the one-time exchange is stored on the link
      (IPv4 as `/32`, IPv6 as `/64` because privacy addresses rotate) and the session works only from it.
      Both can be used together (the pin must also lie inside the ACL). The address is the client
      address as the trusted proxy chain resolves it, the same helper the rate limiter uses (ADR 0086),
      never a raw `X-Forwarded-For` value. A request from another address is refused with a plain
      message (*this screen is not allowed from this network; ask for a new link*), the link stays valid
      for the right address, and one deduplicated audit event `wallboard.session_refused` plus an alert to
      the organisation admins records it (address range, no more). A screen whose address changes (DHCP
      lease, moved cable) simply needs a new link, which is the point of a pin. The stored range is an
      address of staff equipment, not of a participant; it lives only on the link row, is shown to the
      organisation admin on the link list, and is deleted with the link; it never goes in a log line. The link grants the
      same figures as the permission and is therefore a decision of the organisation, not the platform.

### Abuse cases (each gets a test)

| # | Case | Control | Test that fails if it breaks |
|---|---|---|---|
| R1 | Reading another organisation's or group's figures through a report, export, API call or digest | `Scope` from the permission in every query; API client bound to groups; digest recipients re-resolved at send | Matrix sweep: role × endpoint × own/other scope, including CSV, API and digest |
| R2 | Re-identifying a participant from a small cell, by subtracting a total, or by differencing adjacent ranges | Threshold, complementary suppression, fixed cuts, no free pivot, group-total rule, hour cuts only over 7 days or more | Property test on `suppress()`: no output lets a suppressed cell be recovered from totals or from two adjacent ranges; cuts beyond the list are refused |
| R3 | Rebuilding a booking from facts | Allow-list `USAGE_FIELDS`, no booking id or reference, hour grain, no per-booking row in `usage_hour` | Test: no `usage_hour` column or fact field equals a `DATA_FIELDS`/`DERIVED_FIELDS` entry; a booking's id never appears in a fact |
| R4 | Export or API used to pull everything quickly, or to hold the database | Row and day limits, rate limit, `usage_hour` only, read without the write lock, audit, token revocation | Over-limit export refused with a code; rate limit returns 429; export audited with checksum |
| R5 | A digest as an unaudited share | Staff recipients only, link not file, permission checked at send, changes audited | Digest to a staff member without the permission is skipped; no attachment; no address in the job payload |
| R6 | Monitoring individuals (team load) | Off by default, own permission, threshold, no ranking, folding on removal | Team screen refused when the setting is off; a staff member under the threshold is `< 5` |
| R7 | Formula injection in a CSV cell | Same escaping as the audit export; cut codes, not free text | Cell starting with `=`, `+`, `-`, `@` is prefixed |
| R8 | Personal data entering through the backfill or a fact field added later | One allow-list, review question 3 on every step that adds a fact | Test on the fact builder and on the log event `usage.fact` |
| R10 | A display link leaks or is reused after it should have ended | Hash only, shown once, one-time use, expiry, revocation, optional ACL and pin checked on every request, token removed from the URL by redirect and masked in logs, kiosk cookie opens one scope and only the wallboard, audited | Revoked, expired or already used link refused; cookie refused from another network and from another address than the pinned one (v4 `/32`, v6 `/64`), on a poll as well as at the start; spoofed `X-Forwarded-For` ignored; token absent from the logs and from the redirect target; kiosk cookie refused on every other admin route and on *Team* |
| R11 | A frozen wallboard showing old numbers as current | *As of* on every tile, stale state after three failed polls | Fake clock and failing poll: the board switches to the old-data state in text |
| R12 | Script injection through a chart label, tooltip or series name | Labels only from codes and the i18n catalogue, `textContent`, script CSP on report pages only, no data from outside the page | A label containing markup is shown as text; the CSP header on a report page and absent elsewhere |
| R9 | A stale or tampered rollup silently showing wrong figures | `as_of`, lag warning on *System status*, idempotent watermark | Rollup run twice gives the same table; crash between batches loses and doubles nothing |

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `reporting_enabled` | C platform | on | on/off | `settings.platform.manage` | Feature switch (not a protection): off stops facts being written and hides the screens |
| `reporting_retention_months` | C platform | 25 | 13–60 | `settings.platform.manage` | How long `usage_hour` and `usage_staff_hour` rows live; 25 gives two full years and the running one |
| `reporting_event_retention_days` | C platform | 35 | 7–90 | `settings.platform.manage` | How long raw facts stay as the outbox and the rebuild source |
| `reporting_min_cell_size` | C platform | 5 | 3–20 | `settings.platform.manage` | Small-cell threshold; the floor of 3 means it cannot be switched off |
| `reporting_org_min_cell_size` | D organisation | platform value | platform value–50 | `settings.organisation.manage` | An organisation may only raise it; the effective value is the larger of the two, so a later platform rise applies at once |
| `reporting_export_max_rows` | C platform | 100000 | 1000–1000000 | `settings.platform.manage` | Per CSV or API request; separate from the audit export's limits because a report cell is not an audit row, while the CSV code is shared |
| `reporting_export_max_days` | C platform | 400 | 31–1100 | `settings.platform.manage` | Widest date range of one export or API call |
| `reporting_api_page_size` | C platform | 1000 | 100–10000 | `settings.platform.manage` | Cells per API page |
| `reporting_api_rate_per_minute` | C platform | 30 | 5–300 | `settings.platform.manage` | Through the common limiter; the off switch for rate limiting stays `RATE_LIMIT_ENABLED` (B) |
| `reporting_rollup_interval_minutes` | C platform | 15 | 5–240 | `settings.platform.manage` | How often the worker rolls facts up; lag warning on System status at four times this |
| `reporting_staff_view_enabled` | D organisation | off | on/off | `settings.organisation.manage` | Team load screens and `usage_staff_hour` rows (written only when on) |
| `reporting_digest_send_hour` | D organisation | 7 | 0–23 | `reports.digest.manage` | Local hour in the organisation's timezone |
| `reporting_digest_include_numbers` | D organisation | on | on/off | `reports.digest.manage` | Off: the mail holds only the link |
| `reporting_digest_late_hours` | C platform | 24 | 1–72 | `settings.platform.manage` | Latest a missed digest is still sent |
| `reporting_digest_max_subscriptions` | C platform | 20 | 1–200 | `settings.platform.manage` | Per organisation |
| `reporting_usage_events_to_log` | C platform | off | on/off | `settings.platform.manage` | Facts also to the log destinations (point 15) |
| `reporting_wallboard_refresh_seconds` | C platform | 10 | 5–120 | `settings.platform.manage` | Poll interval of the wallboard through the shared fragment mechanism |
| `reporting_wallboard_wait_alert_minutes` | D organisation | 5 | 1–60 | `settings.organisation.manage` | A wait beyond it is marked *long wait* |
| `reporting_wallboard_display_links_enabled` | D organisation | off | on/off | `settings.organisation.manage` | Allows display links for unattended screens (point 20) |
| `reporting_wallboard_link_networks` | D organisation | empty (any) | up to 20 CIDR ranges | `settings.organisation.manage` | Default ACL for display links; a link may narrow it |
| `reporting_wallboard_link_pin_default` | D organisation | off | on/off | `settings.organisation.manage` | Whether *pin to the first address* is ticked by default when a link is created (always changeable per link) |
| `reporting_wallboard_link_max_days` | C platform | 30 | 1–90 | `settings.platform.manage` | Longest life of a display link |

No class A or B setting: nothing here is needed before the database works, tied to the host, or a
kill switch of a protection.

**Fixed on purpose:** the duration buckets (history must stay comparable), the allow-list
`USAGE_FIELDS` and the metric definitions (a change is an ADR), the rollup batch size (a bound on
the write lock, like `booking_rollup.BATCH_SIZE`), the CSV format and column set of
`openplanner.usage/1`, the cut list per report, "never fewer than 3" for the cell threshold, the
rule that a digest has staff recipients only and no attachment, and the WCAG targets of the charts.

Where the settings sit (ADR 0047): on each scope they form one group *Reports*, next to the
retention settings of ADR 0102 (platform: *Advanced settings › Settings*; organisation: the
organisation's settings sub-tab); the exact topic is fixed in `settings_registry` at step 2.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #558 | merged |
| 1 | Domain only: `app/domain/usage.py` (fact kinds, allow-list `USAGE_FIELDS`, metric table, buckets, `suppress()`), `privacy.py` list and test, `docs/reporting.md` generated; no database | #592 | merged |
| 2 | Migration (five tables, expand-only), `record_usage` on `Transaction`, settings and registry entries, facts for booking create, confirm, expire, cancel, move, outcome, suspend/resume | #597 | merged |
| 3 | Facts for calls, queue, direct calls, trial calls, messages, rebooking, slot close and shortfall; PII test over every fact | PR_NUMBER3 | merged |
| 4 | Worker `usage_rollup`, retention pruning, `usage-backfill` CLI and automatic first run, rollup lag on *System status* | #608 | merged |
| 5 | Permissions in the matrix, navigation (*Reports*, *Usage* sub-tab), scope-filtered query layer with suppression, *Volume* screen as tables, matrix sweep, manual nl/en | #619 | merged |
| 6 | SVG charts (`app/web/charts.py`), *Queue and calls*, *Capacity*, *Channels* screens, axe and keyboard checks | | planned |
| 7 | *Team* screen and `usage_staff_hour` (setting off by default, folding on removal) | | planned |
| 8 | CSV export of the screen and the tidy `openplanner.usage/1` file, limits, audit event | | planned |
| 9 | Machine API `/api/v1/reports/usage`, API client scope, `docs/reporting-api.md`, schemathesis | | planned |
| 10 | Digest: subscriptions (*Digest* sub-tab), `reports.digest` job, templates `nl`/`en`, run records | | planned |
| 11 | Facts to log destinations (`usage.fact`, non-staff, hour and bucket only) | | planned |
| 12 | `report-charts.js`: tooltips, focus, series toggles, SVG/PNG download, drill-down links; script CSP on report pages; JS-off and axe runs | | planned |
| 13 | Wallboard: `view=wallboard`, kiosk layout, live tiles on the shared fragment poll, stale state, wait alert, `reports.wallboard.view` | | planned |
| 14 | Display links: creation, hash, expiry, revocation, kiosk session, audit, manual and threat model | | planned |

**Build order (agreed with the product owner, 2026-11-08):** 1, 2, 4, 5, then 3, then 6 to 14. The
*Volume* screen of step 5 needs only the booking facts of step 2, so the first visible screen comes
after four PRs; the metrics that need call facts (U8, U11 and the call metrics) follow with step 3.

**Step 1 notes (no change of a decision):** the domain adds the fact kind `booking.slotted` (one
fact per booking placed in a slot, keyed on the slot start) so that the busiest-hours table (C4)
needs no second hour on `booking.created`; the hour source is declared per fact kind, which the
generated `docs/reporting.md` lists; a booking created by a move carries `reason_code` `move`
instead of a field `is_move`, which keeps the allow-list of point 2 as written. The property tests
of `suppress_table` found that a total must also be hidden when its line has exactly one hidden
cell and no other cell to hide with it; the building block now does that.

**Step 2 notes (no change of a decision):** migration 0090 creates `usage_events`, `usage_hour` and
`usage_staff_hour`; the two digest tables (subscriptions, run records) come with step 10 in their own
expand-only migration, because nothing reads them before then. There are no foreign keys and no unique
index over the cut columns: statistics outlive their group, and the rollup of step 4 is the only
writer under the write lock. Only `reporting_enabled` (class C) is added now; each other setting of
the table arrives in the step that reads it (retention, interval and cell size in steps 4 and 5).
Facts are written for a booking placed (`booking.created` and `booking.slotted`; a move places again,
so C4 counts placements), confirmed, cancelled by a participant, planner, closure or the system,
unconfirmed and lapsed, moved, suspended and resumed. **A booking that ended `completed` or `no_show`
has no producer: nothing in the application sets those statuses**, so U2 and U3 show cancellations and
lapses only until step 3 derives them from the call facts; the PO is told. The count of dropped facts
is a log line (`usage.fact_dropped`, kind only) for now; *System status* shows it with the rollup lag
in step 4. Lead, hold and cancel times in a booking fact are kept to the quarter hour: the booking row
keeps its exact creation and start times for two years, so an exact lead time would let a fact be
matched to one booking by group, hour and locale (the review of step 2 found it, abuse case R3).
Call durations (step 3) are not in the booking row and stay exact for the mean. The host CLI's own
closure and cancel commands write facts whatever `reporting_enabled` says (it reads the portal value
only in the web and the worker process).

**Step 4 notes (no change of a decision):** migration 0097 adds `usage_rollup_state` (the watermark, the
time of the last run, the backfill's completion and cutoff) and `usage_backfill` (one booking id per
booking already done), and rebuilds `usage_events` with `AUTOINCREMENT`: without it SQLite reuses the
largest ids after retention deleted them, and a new fact below the watermark would never be rolled up (the
review of step 4 found it). The cells and the watermark are stored in one transaction. `usage_hour` has no
unique index over its cut columns (a NULL is never equal to a NULL in one): the rollup is the only writer,
under the write lock. A run rolls up at most `MAX_BATCHES` batches of `BATCH_SIZE` facts, retention deletes
at most `PRUNE_BATCH` rows per transaction, and the backfill does a bounded pass per worker loop until it is
complete, so none of them holds the write lock or the worker loop for long; what is left follows on the next
pass. The rollup maps the booking facts only (a declarative table checked against the catalogue at import);
the kinds no step writes yet, and a team member's fact, are passed over by the watermark and count from the
release that maps them. The fact rules (which hour, how a lead time is rounded, what a move carries) are in
`booking_facts`, shared by live recording and the backfill. The backfill makes facts from the columns the
purge leaves: it never invents what the row cannot say (call durations, suspension times), derives who
cancelled (a planner's reason, a closure, else the participant: a cancellation by a team member without a
reason counts as the participant's in U4 and U5 for backfilled hours), and stops at the cutoff, the hour of
the first fact written at that moment (a fact keyed on a slot start says nothing about when it was written;
a cancellation of an old booking between the upgrade and that first fact can still be counted twice, which
the runbook names). Settings added: `reporting_event_retention_days` (C, 35, 7 to 90),
`reporting_retention_months` (C, 25, 13 to 60), `reporting_rollup_interval_minutes` (C, 15, 5 to 240). The
rollup lag is on *System status*; the dropped-fact count stays a log line. Not done, with reason: an
`INSERT ... ON CONFLICT` upsert needs a unique index over expressions, which the model/migration drift test
cannot hold; the read-then-write per cell inside a bounded batch is correct and bounded.

**Step 5 notes (no change of a decision):** `reports.view` (level group) is in the matrix for the superuser,
organisation admin, group admin and auditor; the other permissions of point 11 come with the steps that
need them (export, team view, digest). *Reports* is the fourth entry of *Monitor* (`monitor.reports`), and
*Usage* the last sub-tab under *Overview* of the organisation and of the group, as the ADR placed them. The
organisation page adds up the groups of that organisation inside the reader's scope; the group page shows
one group. `UsageStore` also carries the three report reads (groups in scope, hourly cells, the day counts
from before reporting), every one filtered by the reader's `Scope` through `scope_filter`; a separate port
would have had a single-method unit of work that the one-fake-per-port test cannot tell from any other. The
*Volume* tables are bookings created per day, how bookings ended, lead time, cancellations by participants,
reschedules, confirmation, closures and the bookings from before reporting; **completed and no show stay
empty** (nothing records them) and U8 and U11 wait for step 3. The settings `reporting_min_cell_size` (C, 5,
3 to 20) and `reporting_org_min_cell_size` (D, 3, 3 to 50; the larger of the two applies) are added. Not
done here, with reason: hour cuts and the group-total rule belong to the screens that cut by hour and by
subject (steps 6 and 7); the *Volume* screen has none.

**Step 3 notes (one change to the catalogue, no change of a decision):** the facts of point 6 now have
their producers, each written in the transaction of the change it describes (`Transaction.record_usage`,
which never fails the action) and each pure builder in its own module.

- *Calls and trial calls.* `app/domain/call_facts.py` turns a timeline event and the call status before and
  after it into facts; `call_usage.record_call_event` loads what it needs and is called just before the event
  is written at the three places that append one: `IntegrationService._record` (webhooks and jobs),
  `MeetingStatusService._poll` and `BookingService.leave_direct`. A fact follows a *change of the status*, so
  a replayed webhook (deduplicated by its key) or a repeated poll writes nothing twice; a trial step counts
  once per booking (the booking's earlier events are read), a trial failure every time. A failure reaches the
  fact only as `task.`, `meeting.` or `integration.` plus a code of the closed list `FAILURE_CODES`
  (`rejected_403` is `rejected`); a provider's text, however it was formed, becomes `other`. Durations are
  exact seconds. A work item that is created twice (a fallback to the own queue that ends in a contact-center
  task) counts as two `call.created`; the rare case is accepted.
- *Q4, the guest's wait.* A new fact kind `call.guest_waited` carries `host_joined_at - max(start,
  guest_joined_at)` when positive, for scheduled calls; Q4 now lists it instead of `call.host_joined`
  (the metric had no data before, so `METRIC_VERSION` stays 1). `CallStatus` gains `guest_joined_at` and
  `peak_participants` (the most people seen, kept when the meeting ends, which U11 needs); the poll cannot
  tell who came first when it sees both at once and then claims no wait.
- *Q9.* The `participants` field of the `call.created` fact of a direct call carries the direct calls waiting
  or connected in the group right after it; the peak of an hour is the maximum of these. The allow-list is
  unchanged.
- *Queue.* `QueueService._claim` and `release` write `queue.claimed` (seconds from the booking becoming
  visible, `queue_visible_from_minutes` before the start or its creation, to the claim) and
  `queue.released`. No agent, not even a pseudonym, is in the fact; a team member's `StaffFact` stays step 7.
- *Messages.* The job runner writes `message.sent` after a successful send and `message.failed` when it gives
  up (a retry that works is one `message.sent`), in a transaction of its own, with the channel kind and a
  purpose from the closed list `message_usage.PURPOSES`; never a recipient, a text or the sender's answer.
  The team's calendar invite is no message to a participant and writes nothing. A failure to write is logged
  as `usage.message_failed` and never changes the job.
- *Rebooking.* `rebook.proposed` (planner-asked; the reason is the planner's fixed reason; the assistant of
  ADR 0057 is not built, its facts will carry the actor class `system`), `rebook.accepted` when the
  participant moves a booking that awaits a new time (the quarter hours since the request; migration 0099
  adds `bookings.rebook_requested_at`, a request made before it has no time) and `rebook.expired` when it
  lapses. A planner moving a waiting booking is no acceptance. The accepted fact has no proposer, so the
  planner-asked and assistant-proposed split of U8 is on the proposed count only until the assistant exists.
- *Slots and outcomes.* A worker pass (`slot_close_service.SlotCloser`, on the rollup's interval, so no new
  setting) closes every slot that ended two hours ago (`CLOSE_DELAY`, fixed on purpose: late reports) and
  writes, keyed on the slot start and in one transaction with its watermark
  (`usage_rollup_state.slots_closed_until`): `slot.closed` (capacity snapshot, bookings that count),
  `slot.shortfall` (`open`, or `accepted` by a planner at those counts, from the staffing as it is when the
  slot closes), `booking.outcome` and `call.not_picked_up`. A planner's bulk request that ends a slot's
  shortfall writes `slot.shortfall` `resolved`; a shortfall that disappears any other way is not seen, so
  `resolved` is a lower bound. The first pass after the upgrade only sets the watermark: earlier slots are not
  closed afterwards. Slots exist only when they had a booking, so hours nobody booked are not in C1 to C3.
- *Outcome rule.* Nothing sets a booking's status `completed` or `no_show`, and this step does not add a
  writer: the outcome is derived from the call (the table is in `docs/reporting.md`). A scheduled booking is
  `completed` when the participant was seen and the host joined, `no_show` when the participant was not seen
  in a meeting that was observed, and has no outcome when no meeting was made or it failed, or the
  participant came and nobody received them (that is `call.not_picked_up`). A direct call is `completed` or
  nothing. `no_show` means *no join was observed*: where the platform's webhooks are not set up and nobody
  opened the agenda, a join cannot be seen. The ADR text of U3 should be read with that. `completed` and
  `no_show` cannot be backfilled, and slots that ended before the upgrade stay without them.
- *Not picked up.* A confirmed booking that reached staff (a work item, a fallback, or an own queue) and
  that no host joined and no agent claimed within the group's `not_picked_up_minutes` of its start; written
  once per booking by the slot closing.
- *Rollup.* Every fact kind now has a feed (a test checks it); the feeds read `figure` and `tag` as well as
  the cuts, so C1 is two cells (`booked`, `capacity` in `reason_code`), C2 a fill class, M1 `<purpose>.sent`
  or `.failed`; `docs/reporting.md` lists them. Migration 0099 is expand-only (`call_status.peak_participants`,
  `call_status.guest_joined_at`, `bookings.rebook_requested_at`, `usage_rollup_state.slots_closed_until`).
- *Settings.* None added. The hard-coded values are `CLOSE_DELAY` (two hours), `BATCH_SLOTS` (200, a bound on
  the write lock) and the closed code lists above.

Each step is one PR and its description answers the eight review questions. The ones that differ
per step:

- **Steps 1:** endpoint or job (Q1): none; PII risk (Q3): none: pure functions; failing test (Q4): allow-list test, `suppress()` properties.
- **Steps 2–3:** endpoint or job (Q1): no route; transactions write facts; PII risk (Q3): a fact field that is not allow-listed; failing test (Q4): fact-builder test per kind, mirror of `DATA_FIELDS`.
- **Steps 4:** endpoint or job (Q1): job `usage_rollup`, CLI `usage-backfill`; PII risk (Q3): backfill reading a purgeable column; failing test (Q4): rollup idempotence; backfill reads none of `PURGED_FIELDS`.
- **Steps 5–7:** endpoint or job (Q1): `/admin/reports`, `/admin/.../usage/*`, `require_permission` on each; PII risk (Q3): a screen cut that rebuilds a small cell; the *Team* screen; failing test (Q4): matrix sweep; suppression property test; setting-off refusal.
- **Steps 8–9:** endpoint or job (Q1): export routes, `/api/v1/reports/usage`; PII risk (Q3): CSV cell content; API token reaching another group; failing test (Q4): limit and audit tests; cross-group API test; formula test.
- **Steps 10:** endpoint or job (Q1): job `reports.digest`, subscription forms; PII risk (Q3): an address or a figure in a job payload or a log; failing test (Q4): no-attachment, no-address-in-payload, permission-at-send tests.
- **Steps 12–14:** endpoint or job (Q1): `view=wallboard`, display-link routes and the kiosk session (`require_permission` or the kiosk cookie on one route); PII risk (Q3): a chart label or tooltip, a token in a URL or log; failing test (Q4): JS-off page test, label-as-text test, revoked-link and redirect tests, stale-board test.
- **Steps 11:** endpoint or job (Q1): no route; PII risk (Q3): the `usage.fact` log event; failing test (Q4): masked-stream test.

## Consequences

- Analysts get durations, queue method, subject, lead time, no-shows and utilisation per hour, which no
  existing table keeps after the purge. The price is a second write per state change (one small
  insert in the transaction that already holds the lock) and three tables to retain and document.
- History before the release is coarse: outcome counts per group and day, nothing else (point 5).
  The manual and the screens say so.
- The statistics are not a loophole around the purge. They hold counts per hour and per cut, with a small-cell
  rule, never a line per booking; the retention matrix in `docs/dataflow.md` lists them with their term
  (`reporting_retention_months`), and the DPIA input says what a cell of one can still reveal.
- Per-person figures for team members are possible but off by default and kept apart, because they are
  another group of people's personal data.
- Screens that are static HTML and SVG with a table beside each chart keep the admin without a
  script framework and within WCAG 2.2 AA, at the cost of no interactive zoom or tooltips; an analyst
  who wants those uses the CSV or the API in their own tool.
- Interactive charts and the wallboard are enhancements on a complete baseline, so a browser without scripts,
  a screen reader and a print-out still get every number. They add one small script of our own, a
  second place where untrusted text could reach the page (R12), and a token-based way to open a
  screen that nobody signs in at, which the organisation must choose to allow (R10).
- Parquet, OData and a database view for BI tools are refused; if a customer needs one, a new ADR weighs the
  dependency and the surface against the API.
- The group page still has ten tabs until ADR 0047 step 4, and this ADR adds none; the *Usage* sub-tab
  is placed under *Overview* so the six-tab rule holds afterwards.
- Open points that the product owner may still change: the proposed role holders (point 11), the digest
  being staff-only, and the default of the team screen being off.
