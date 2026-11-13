# Immediate intake and check-in on the same pool (build brief of ADR 0121 step 7)

Status: design of step 7 of ADR 0121 (*P7: immediate intake and check-in on the same pool*), written against `main` after step 6b (#776). ADR 0121 points 4 to 7 and 11, the decisions D1 and D3 of the product owner, ADR 0122 and its decisions P1 to P8, and the briefs of steps 4, 5 and 6 (`docs/service-flows-skill-enforcement-design.md`, `docs/service-flows-requests-design.md`, `docs/service-flows-contact-center-contract-design.md`) are taken as given. Section 10 lists the choices this brief made itself, each with the default it builds; none of them needs the product owner before the build, all of them are open to a later decision.

## 1. What step 7 adds, and what it leaves alone

| Adds | Leaves alone |
|---|---|
| **Immediate intake**: a participant asks for help *now* under a flow whose intake modes include `on_demand`; no slot, no booking; a `ServiceRequest` with intake `on_demand` and, when anything was asked, the request-level personal-data record of D1 (its first writer) | The legacy direct call (ADR 0019, 0026, 0072): a booking of kind `direct`, its own cap `direct_call_max_concurrent`, its meeting and its waiting page. It keeps serving the legacy `direct` flow and every service without a pool flow |
| **Admission** of immediate work on the pool the flow draws on: a waiting limit per pool (`capacity_pools.max_waiting`), a look-ahead (`on_demand_admission_lookahead_minutes`) and the earliest start as a forecast (ADR 0122 section 4) | Appointment admission, the grid and the booking path of ADR 0122 step 5 |
| **The claim acquires the unit**: claiming immediate work runs `check_start(intake=on_demand)` under the lock and writes the occupation as the work item's own (`assigned`, pool and planned end set at the claim); the start checks again with its own unit left out | The claim of an appointment (no new commitment: the booking's hold is it) |
| **Check-in** of an appointment whose flow needs an explicit check-in (`in_person`): the request is made at the check-in if it has none, with `arrived_at`; a second check-in changes nothing (A15); such work can be claimed only after the check-in | Flows with automatic readiness (`telephone`, `none`): ready at their start, as in step 5 |
| **The participant's page** of an immediate request, its status fragment and withdrawing, under an opaque reference and a cookie of its own (7b) | The participant link session of ADR 0093 (it holds bookings only) |
| **Immediate work in the own queue** (7b): listed under the appointments, oldest first, first name only, skills named when missing (choice S3), claim, start, complete, release | Every row of the existing queue |
| **Retention end to end**: the record is deleted at `min(resolved_at, watchdog_at) + 24 h` by the watchdog round, swept nightly, and counted on the retention overview (follow-up 5d) | The purge of booking data and its holds (`app/domain/retention.py`) |

The rollback gate stays shut (`ROLLBACK_SAFE = False`): in production no non-legacy flow can be activated, so nothing in this step is reachable on a host. The tests open the gate as steps 5 and 6 do.

## 2. Data model (migration 0115, expand only)

Number 0115, revising 0114 (`work_items.channel`, #776). Nothing is dropped, renamed or tightened.

| Table / column | Type | Notes |
|---|---|---|
| `capacity_pools.max_waiting` | integer NULL, check `1..1000` | How many immediate requests may wait for the pool at once; NULL is no limit (ADR 0122 section 4: configuration of one object, not a setting) |
| `service_requests.pool_id` | FK `capacity_pools` RESTRICT, NULL | The pool an immediate request draws on, fixed at acceptance from its revision (A23); NULL for an appointment (its slot carries the pool). Index `ix_service_requests_pool_handling (pool_id, handling)` for the waiting count |
| `service_requests.subject` | string(64) NULL | The subject code chosen at intake (ADR 0081, closed list per service), for the skill requirement of its SubjectFlow; an appointment's subject stays on its booking |
| `service_requests.arrived_at` | UTC NULL | Attendance: the check-in of an appointment, the acceptance of an immediate request |
| `service_requests.public_ref` | string(32) NULL, unique | Names an immediate request in the participant's address; authorises nothing (as a booking's public reference, ADR 0093 point 1) |
| `service_requests.access_hash` | string(64) NULL | `sha256` of the random value in the participant's cookie; emptied with the personal data |
| `service_requests.intake_key_hash` | string(64) NULL | `sha256` of the random key of the intake form (A16); unique with `group_id`; emptied with the personal data |

The domain keeps the pool on immediate requests only (`ServiceRequest` refuses an immediate request without one and an appointment with one); the database adds no check for it, since a check would copy the table. `pool_id` has no database foreign key, like `slots.capacity_pool_id` (migration 0111), for the same reason; `max_waiting`'s bounds are checked by the adapter. The work item needs no column: an *assigned* immediate work item carries `pool_id` and `planned_until` from its claim, with `started_at` still empty.

`open_occupations` (the work items' part of `get_pool_commitments`) returns, besides started work that has not ended, every assigned immediate work item that has not ended, over `[assigned_at, planned_until)` with key `("request", id)`. When the item starts, the commitment moves to `[started_at, planned_until)` under the same key, so the pool counts one unit throughout.

**Previous image (ADR 0116):** it does not read the new columns; it never meets an immediate request or a check-in, because only an active non-legacy flow writes them and the gate keeps those shut. Downgrade refuses while an immediate request taken in on a pool exists (it would lose its pool), then drops the columns and the indexes.

The hashes are not personal data, but they let a browser find a request again: the deletion of the request-level record empties them, and the participant's page closes at the deletion moment whatever the hashes say (`page_open`).

## 3. Lifecycle

The states of step 5 are unchanged; step 7 adds two entries and one condition.

```
immediate intake ─► request on_demand, queued, arrived_at = accepted_at, watchdog_at = accepted + hours
                     ─► claim (check_start on_demand, the unit is acquired, lease) ─► assigned
                     ─► start (check_start again, own unit left out) ─► in_progress ─► complete
participant withdraws (not started) ─► cancelled; work item cancelled, unit released once
check-in of an appointment (explicit check-in flows) ─► request of the booking, arrived_at
                     ─► claim allowed from now on (else not_checked_in)
```

- **One request per appointment** (A15): a check-in makes the request of the booking when it has none, exactly as the claim does in step 5; the booking's unique request (`service_requests.booking_id` UNIQUE) makes a second check-in or a check-in after a claim a no-op. The request's commitment key is the booking's, so the pool counts one unit.
- **Lease expiry** of an immediate work item releases its unit: the work item ends `lease_expired` (`ended_at` set, the one release) and the request returns to `queued`. An assigned work item still holds its unit between its lease's end and the next watchdog round (at most one minute): conservative, never a double sale.
- **The watchdog** treats an immediate request like any request without a booking (its phase is always *active*): unstarted at `watchdog_at`, `expired`; started, `recovery_required` with its unit kept (A34).

## 4. Admission and starts

```
accept(service, flow, contact, subject, key)
  locked: resolve the flow on the on_demand path (gate; legacy goes to the direct call)
          a request with this key in this service, not finished? ─► the same one (A16)
          closure of the service now? ─► group_closed
          pool: counted, of the organisation, not archived, a time zone ─► else on_demand_unavailable
          waiting = queued + assigned immediate requests of the pool; max_waiting reached ─► on_demand_full
          earliest_start(supply, commitments, now, duration, buffer, now + look-ahead)
              None ─► on_demand_unavailable          (nobody staffed for it, or no fit in time)
          store request + record (D1) ─► expected start, place in line
claim (staff, queue.work, skills)                    start (holder)
  locked: check_start(on_demand) at now ─► refused: start_refused      locked: check_start without the own unit
          work item assigned, pool, planned_until = now + length + wrap-up       ─► refused: start_refused
```

- **Waiting is not treatment** (invariant 4): a waiting request holds no unit; only the claim acquires one. The look-ahead makes sure nobody is admitted to a line that cannot be served soon; `max_waiting` bounds the line.
- **Appointments are protected** (invariant 5): `check_start` with intake `on_demand` counts every holding reservation and every running overrun; it has no conflict outcome for immediate work (P8 applies to an appointment that is due, never to immediate work). There is no switch.
- **Blocks without appointment** (P7): only units of blocks that offer *without appointment* or *both* count for immediate work, and immediate work never counts units of an appointments-only block.
- **Length and wrap-up**: the flow revision's `duration_minutes` and the organisation's `treatment_buffer_minutes`, read at the claim and fixed into `planned_until`.
- **The expected start** is `earliest_start` on the planned ends, a forecast and never a promise (ADR 0122 section 9).

## 5. Error codes

| Code | HTTP | When |
|---|---|---|
| `on_demand_full` | 409 | the pool's waiting limit is reached |
| `on_demand_unavailable` | 409 | nobody is staffed for work without appointment within the look-ahead, no start fits in it, or the pool offers nothing |
| `not_checked_in` | 409 | claiming an appointment of a flow with an explicit check-in before its check-in |
| `request_not_found` | 404 | staff name an immediate request that is not in the service (the same answer for one of another service) |

Existing codes reused: `group_closed`, `flow_not_available`, `start_refused`, `lease_expired`, `request_in_progress`, `request_not_started`, `not_claimed`, `not_eligible`, `already_claimed`, `link_required` (the participant's page without its cookie, the same answer whether or not the request exists). Texts are `error.<code>` in nl and en.

## 6. Settings (ADR 0042)

| Key | Class | Default | Bounds | Permission | Read |
|---|---|---|---|---|---|
| `on_demand_admission_lookahead_minutes` | D organisation | 120 | 15–480 | the registry's class D permission (held by the same roles as `capacity.manage`) | at every intake |

`capacity_pools.max_waiting` is configuration of one pool (ADR 0122 *Settings*), edited on the pool's page under `capacity.manage` and carried by the configuration package. **Fixed on purpose:** the protection of appointments against immediate work (a protection is never C or D); one active request per browser and service on the participant's page (an abuse bound); the size of the public reference, the cookie value and the intake key (crypto parameters of ADR 0093).

## 7. Screens and pages

### Participant (7b)

| Address | What | Rule |
|---|---|---|
| `GET /g/{slug}/direct` | When the service's on-demand flow (default, or `?flow=`) is a pool flow: the immediate form, with the line now (waiting, expected start); the legacy direct-call form otherwise | The same address as today, so links from a client's site keep working; the flow decides |
| `POST /g/{slug}/direct` | Accept: honeypot, same origin, rate limit `booking_limit`, the service's form fields (closed catalogue of ADR 0055, email per flow `collects_email`), a hidden intake key (A16); sets the cookie `op_req` and redirects to the page | No personal data in the address or the log |
| `GET /r/{ref}` and `/r/{ref}/status` | Status: waiting (place, expected start), being helped, done, withdrawn, expired; the fragment for `live-region.js` (204 while unchanged); without script a refresh button | No time limit (2.2.1); first name only, never email or phone |
| `GET`/`POST /r/{ref}/leave` | Withdraw while not started | Same origin, rate limited |

Texts say *verzoek / request* and the service's own names; no internal words (pool, unit, work item).

### Staff (7b, ADR 0047)

| What | Where | Permission |
|---|---|---|
| Immediate work: list, *pick up*, the claimed request (first name, answers an agent may see), *Start*, *Complete*, *Release* | service → Agenda → Queue (exists, `wide`), a second table under the appointments; the claimed request at `/admin/groups/{slug}/queue/requests/{id}` as a sub-page of the queue | `queue.work`; *Complete* in recovery and *Release* of another's claim: the supervisor rule of `release` |
| *Check in* on a row of a flow with explicit check-in | the same queue row | `queue.work` |
| `max_waiting` | organisation → Overview → *Skills and capacity* → the pool's page (exists) | `capacity.manage` |
| Request-level records holding data and overdue (follow-up 5d) | the retention overview (exists), two more counts per service | `retention.view` |

No new section, top-level entry or seventh tab. No manual topic changes (existing topics wait for the next major release).

## 8. Threat model rows (`docs/threat-model.md`, 7b)

| Flow | Threat | Mitigation | Test |
|---|---|---|---|
| Immediate intake | bots fill the line (slot exhaustion without slots) | rate limit `booking_limit` (the common limiter), honeypot, same origin, `max_waiting`, the look-ahead (one open request per browser is a convenience, not a bound: a bot keeps no cookie) | `test_waiting_limit_refuses`, `test_a_browser_holds_one_open_request`, route rate-limit test |
| Immediate intake | a retried POST makes two requests (A16) | the intake key, unique per service, returns the same request | `test_a_retried_intake_returns_the_same_request` |
| Request page | someone reads or withdraws another's request (A31) | random 16-byte reference that authorises nothing; the cookie's hash on the request; `link_required` the same for a missing and a foreign request | `test_the_page_needs_its_cookie` |
| Request page | personal data in an address or a log | reference only in the address; the record's fields are masked by the one list (`privacy.REQUEST_DATA_FIELDS`); the page shows the first name only | `test_no_personal_data_in_the_address` |
| Claim | immediate work takes a unit reserved for an appointment (A17) | `check_start(on_demand)` under the lock at the claim and the start; no conflict outcome for immediate work | `test_immediate_work_never_takes_a_reserved_unit` |
| Check-in | a double check-in counts a reservation twice (A15) | one request per booking (unique), the booking's key | `test_a_double_check_in_counts_once` |
| Retention | the first public writer of request-level data keeps it too long | watchdog, deletion at `min(resolved, watchdog) + 24 h`, nightly sweep and backlog alert, counts on the overview | `test_immediate_record_is_deleted_end_to_end` |

## 9. Acceptance

| ID | Proved | How |
|---|---|---|
| A15 | yes | check-in twice and claim: one request, one work item, one unit |
| A16 | yes | the same intake key twice: one request |
| A17 | yes | the 09:55 example end to end: the request is accepted with expected start 10:30, its claim at 09:55 is refused, at 10:30 it is allowed |
| A18 | yes | an immediate start is refused during an overrun; an appointment that is due starts with the conflict recorded (step 5, unchanged) |
| A20 | yes | an immediate request without name or email is accepted, its record is `not_collected`, nothing is invented |
| A31 | yes | the page needs its cookie; the reference names, never authorises |
| A33 | yes | a never-claimed immediate request expires at its watchdog and its record is deleted 24 h later |
| A11, A12 | yes | an immediate claim and an appointment admission on one pool share its units; another pool is untouched |
| A21, A22 | not for immediate work | an immediate request is never dispatched to the contact center in this step (choice I8); readiness says so |
| A32 | partly (7b) | route tests in nl, scripts off (plain forms, a refresh button); the e2e suite (keyboard, axe, 320 px) once a pool flow can be active on the e2e stack (follow-up row 32) |

## 10. Choices this brief made (for the product owner; built with the default)

| # | Question | Default built |
|---|---|---|
| I1 | Who checks an appointment in? | Staff, from the own queue (`queue.work`). Self check-in by the participant, a kiosk or a ticket printer is follow-up row 20 (devices) or a new row: it needs proof of presence |
| I2 | Is a check-in required before the claim? | Only for flows whose readiness is an explicit check-in (`in_person`); `telephone` and `none` are ready at their start |
| I3 | From when may an appointment be checked in? | While the queue shows it (`queue_visible_from_minutes` of the service, existing) and until its end; no new setting |
| I4 | How does the participant find their immediate request again? | A cookie of its own (`op_req`, random value, hash stored) and a random reference in the address; no email, no token in a URL. A "your turn" email or SMS is a follow-up row |
| I5 | Ticket numbers | None in this step: the page shows the place in line and the expected start. Printed or displayed tickets are follow-up row 20 (A31 already holds: the reference reveals nothing) |
| I6 | When is an immediate request refused? | Service closed; the pool's waiting limit reached; nobody staffed for work without appointment, or no start that fits, within the look-ahead |
| I7 | May the participant withdraw? | Yes, until treatment starts; an assigned work item is then cancelled and its unit released once |
| I8 | Immediate work of a contact-center flow | Not in this step: an on-demand flow dispatched to the contact center is `not_built_yet` in its readiness, so it cannot be activated. The contact center needs a reference for work without a booking and a matching late-event rule; follow-up row 30 |
| I9 | Order in the line | First come, first served (accepted time); work an agent lacks skills for stays visible with the skills named (S3) |
| I10 | Watchdog of immediate work | Acceptance plus `request_unresolved_hours` (step 5's setting); no end-of-day expiry. An organisation that wants a shorter bound lowers that setting (1 to 168 hours) |
| I11 | The address of the page | The existing `/g/{slug}/direct`: the flow decides between the legacy direct call and immediate intake, so a client's links keep working |
| I12 | One open request per browser | Yes: a browser that holds an open immediate request of the service is shown that request instead of the form. Matching by email address is not done (it would read personal data to refuse) |
| I13 | The fields asked | The service's own form (ADR 0055 closed catalogue) with the flow's `collects_email`; nothing else, never a free-text field the service did not add |

## 11. Pull requests

| PR | Content | Acceptance |
|---|---|---|
| 7a | This brief; migration 0115; `ServiceRequest` fields; `app/domain/immediate_intake.py` (admission, check-in rules, the participant's view of a request); `app/domain/immediate_requests.py` (accept, status, withdraw) and `app/domain/request_queue.py` (staff: list, claim, start, complete, release); check-in in `QueueService` and `RequestWork`; the assigned occupation in `open_occupations`; `max_waiting` in the configuration package; the on-demand gate (`pool_flows_bookable`), the legacy direct call refusing a pool flow, readiness `not_built_yet` for on-demand plus contact center; setting `on_demand_admission_lookahead_minutes`; error texts nl and en; ADR 0121 rows 7a, 7b and 30 | A11, A12, A15, A16, A17, A18, A20, A33 in the domain and the SQL adapter |
| 7b | The participant's pages and cookie; immediate work and check-in on the queue page; `max_waiting` on the pool page; the retention overview counts (5d); `e2e/pages.json` entries wait for an active pool flow on the e2e stack (follow-up row 32); `docs/threat-model.md`, `docs/dataflow.md`, `docs/architecture.md`, `docs/widget.md` (unchanged: the widget books appointments only) | A31, A32, the routes' abuse cases |
