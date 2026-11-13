# Requests, work items and their retention (build brief of ADR 0121 step 5)

Status: design of step 5 of ADR 0121 (*P5: generic request execution without video, optional contact data, watchdog and deletion*), written against `main` after ADR 0121 step 4a (#762) and ADR 0122 step 5 (#761). The decisions of the product owner (ADR 0121 point 7 and point 11, choice D1 of `docs/service-flows-handover.md`) are taken as given; section 9 lists the choices this brief made itself, each with the default it builds. The capacity rules are those of ADR 0122 and `docs/service-flows-capacity-design.md` (section 4: lease, start, release exactly once); this brief only says where they are stored and wired.

## 1. What step 5 adds, and what it leaves alone

| Adds | Leaves alone |
|---|---|
| `ServiceRequest`: the identity of one request, with an optional reservation (the existing booking) | Every legacy flow, every booking without a flow and every booking on a service's own staffing: no request is made for them, the queue, the claim and the purge behave exactly as before |
| `WorkItem`: one attempt of staff to handle the request, with a lease before treatment starts and an occupation of the pool while it runs | The video, contact-center and direct-call paths (no non-legacy flow can choose them: `BUILT_KINDS`, `BUILT_DISPATCH`) |
| The request-level personal-data record of decision D1, for a request without an appointment | `Booking` as the store of the personal data of an appointment (D1: a resolver reads one of the two, never both) |
| Start and complete in the own queue for a flow without video | The rollback gate `ROLLBACK_SAFE`: it stays `False`, so in production no non-legacy flow can be activated or booked and nothing in this step is reachable |
| The watchdog and the deletion of ADR 0121 point 7; `RETENTION_BUILT` turns true | The participant's choice between several flows (the step 3a row "choice screen ... follows in step 5" becomes follow-up row 5c) |

The first complete demonstration (D3) is one flow without video on the own queue: a participant books an appointment on a counted pool, the appointment becomes due, an operator with the right and the skills claims it, starts it (the pool is checked again), completes it, and the data goes on time. Because `ROLLBACK_SAFE` is false, the demonstration runs in tests that open the gate (as #761 does), not on a host.

## 2. Data model (migration 0113, expand only)

Number 0113, revising 0112 (`skills.contact_center_code`, #763).

### 2.1 `service_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `organisation_id` | FK `organisations`, RESTRICT | the scope of every query |
| `group_id` | FK `groups`, RESTRICT, indexed | the service; a request is never moved to another service |
| `flow_id`, `flow_revision` | integer, NOT NULL | the revision it was made under (ADR 0121 point 4); FK to `flow_revisions` (RESTRICT): a revision a request refers to cannot be deleted |
| `intake` | `appointment` or `on_demand` | `IntakeMode`; step 5 writes `appointment` only |
| `booking_id` | FK `bookings`, CASCADE, NULL, UNIQUE | the reservation; one appointment never yields two requests (A15). CASCADE because the booking-row rollup of ADR 0102 deletes old purged bookings |
| `handling` | string, check list | `HandlingState`, section 3 |
| `created_at` | UTC | |
| `watchdog_at` | UTC, indexed | the reservation's end (or, on demand, the acceptance) plus `request_unresolved_hours`, fixed when the request is made, like the wrap-up on a pool slot |
| `resolved_at` | UTC, NULL | when it reached a terminal state |
| `personal_data` | `not_collected`, `present`, `deleted` | the state of the request-level record only (section 4) |
| `data_deleted_at` | UTC, NULL | when the request-level record was deleted |

### 2.2 `work_items`

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `request_id` | FK `service_requests`, CASCADE, indexed | |
| `state` | string, check list | `WorkItemState`, section 3 |
| `assigned_to` | string(64), NULL | the agent's pseudonym (`tokens.agent_pseudonym`), never a name; a derived field, emptied by the deletion |
| `assigned_at` | UTC | |
| `lease_until` | UTC, NULL | `assigned_at + assignment_start_lease_minutes`; only while `assigned` |
| `started_at` | UTC, NULL | treatment start |
| `pool_id` | integer, NULL | the counted pool the occupation holds a unit of; set at the start |
| `planned_until` | UTC, NULL | `started_at + duration + wrap-up`, fixed at the start |
| `start_conflict` | boolean | the start went ahead during a colleague's overrun (ADR 0122 decision 11, P8) |
| `ended_at`, `end_reason` | UTC, string, NULL | when and why it stopped being active (`completed`, `released`, `lease_expired`, `expired`, `cancelled`) |

A partial unique index `uq_work_items_active (request_id) WHERE state IN ('assigned', 'in_progress', 'recovery_required')` keeps at most one active work item per request; the claim relies on it as a backstop of its locked check.

**The occupation is part of the work item** (refines the `pool_occupancies` sketch of the capacity design, section 2.1): in step 5 every occupation belongs to exactly one work item, so a separate table would only add a join and a second place to release. A started work item with `pool_id` set and not ended is a commitment `in_progress=True` over `[started_at, planned_until)` with the request's key (`("booking", id)` for an appointment, `("request", id)` without one); `get_pool_commitments` returns it after the booking's hold, so `one_per_key` keeps one unit (invariant 3, A15). Ending the work item (`ended_at` set by a conditional update `WHERE ended_at IS NULL`) is the release, exactly once (invariant 6). A work item in `recovery_required` is not ended: its unit stays held (A34). Step 7 adds the occupation of an *assigned* on-demand work item to the same query.

### 2.3 `request_personal_data` (decision D1)

| Column | Notes |
|---|---|
| `request_id` | PK, FK `service_requests`, CASCADE |
| `first_name`, `last_name`, `email`, `phone`, `form_answers` | the same names, types and rules as on `bookings`; every one may be NULL (A20: what a flow does not collect is not stored) |

The columns are `privacy.REQUEST_DATA_FIELDS`, a subset of `privacy.DATA_FIELDS` by name, so the log mask, the semgrep rule and the purge list stay one list (a test checks the subset and that the table has exactly these personal columns). The deletion **deletes the row** and sets `service_requests.personal_data = 'deleted'` and `data_deleted_at`; a deleted record and a record that was never collected are told apart by that state, never by a missing row (ADR 0121 point 7: missing data is not deleted data).

Step 5 builds the record, its resolver, its deletion and its place in the retention overview; nothing in step 5 writes one from a public path, because every request of step 5 has an appointment and keeps its data on the booking (D1). The first writer is the on-demand intake of step 7. The adapter method that stores a request takes the record, so step 7 adds a caller, not a rule.

### 2.4 Not added

No column on `bookings` and no change of meaning of an existing column. A pool booking's hold is still derived from its status (ADR 0122 point 4); the request does not copy it.

## 3. Lifecycle (`app/domain/service_requests.py`, pure)

```
HandlingState:  queued ─claim─► assigned ─start─► in_progress ─complete─► completed
                  ▲   release / lease expiry ┘           │
                  └──────────────────────────            └─watchdog─► recovery_required ─complete─► completed
                queued / assigned ─watchdog─► expired      any non-terminal, booking no longer holds a place
                                                           and not started ─► cancelled
WorkItemState:  assigned → in_progress → completed | recovery_required → completed
                assigned → released (release) | lease_expired | expired | cancelled
```

- **Terminal** handling states: `completed`, `cancelled`, `expired`. A terminal request never changes again (`check_transition` refuses).
- **Created at the first claim.** A request of an appointment is made inside the locked claim when the booking is on a counted pool (its slot carries a pool) and has none yet; the claim opens its first work item `assigned`. Before the claim the booking alone is the reservation, so no booking path (book, move, cancel, confirm, rebook, closure, the participant's link, an older image) needs to know about requests. A booking that nobody claimed gets its request from the watchdog, already `expired` (A33).
- **Lease.** A claimed work item that has not started holds the claim until `lease_until`; a start after it is refused (`lease_expired`) and the watchdog round releases it: work item `lease_expired`, request back to `queued`, the booking's claim cleared (ADR 0122 section 4: the appointment returns to the queue and keeps its hold). Lease expiry never touches a started work item.
- **Start** (`QueueService.start`, locked): the holder of the claim, eligible again (`WorkEligibility`, A35), the booking still confirmed and the lease not expired; then `check_start(supply, commitments, now, duration, buffer, intake=APPOINTMENT, own_key=("booking", id))` on the booking's pool: `REFUSED` is the error `start_refused`, `OVERRUN_CONFLICT` starts with `start_conflict` recorded (P8), `MAY_START` starts. The duration is the booking's own (`slot.end - slot.start`), the buffer is the slot's (`occupied_until - end`), both fixed when it was booked (A23).
- **Complete** (`QueueService.complete`, locked): the holder of the claim, or, for `recovery_required`, anyone who may view the service's bookings (the supervisor rule of `release`). The work item ends (`completed`), the request becomes `completed` with `resolved_at`, the occupation is released and the booking's status becomes `completed`: explicit evidence (A27), not an observation of a meeting.
- **Release** (existing, extended): a work item that has not started ends `released` and the request returns to `queued`. A started one cannot be released (`request_in_progress`); it can only be completed, so a running treatment never disappears from the pool unnoticed.
- **Cancellation of the booking.** Any path, including an older image, only changes the booking's status. The watchdog round sees a request whose booking no longer holds a place: not started becomes `cancelled` (its work item `cancelled`, the claim cleared); started stays as it is (the treatment really occupies someone) until completed or the watchdog deadline.

### 3.1 The watchdog (`watchdog_at`)

`watchdog_at = reservation end + request_unresolved_hours` (on demand: acceptance + the same), fixed at creation. When it has passed and the request is not terminal:

| Active work item | Outcome |
|---|---|
| none, or `assigned` | request `expired`, work item `expired`, claim cleared |
| `in_progress` | request and work item `recovery_required`; the occupation stays (A34) |
| `recovery_required` | unchanged |

`watchdog_outcome(request, item, booking_holds_place, now)` is the one pure function; the round applies its answer with a conditional update per request (`WHERE handling = :seen`), so a claim or completion in between wins and the request is looked at again in the next round.

### 3.2 Deletion moments (fixed, ADR 0121 settings: *fixed on purpose*)

| Data | Deleted at |
|---|---|
| Personal data on the booking of an appointment request | the booking's existing purge moment (`purge_pii`, `retention.purge_moment`): slot end + 24 h and the holds of `retention.py`. Unchanged, and earlier than the watchdog bound for every default |
| The work item's `assigned_to` of an appointment request | together with the booking's purge (`purge_personal_data` empties it in the same transaction) |
| The request-level record and `assigned_to` of a request without an appointment | `min(resolved_at, watchdog_at) + 24 h` (`data_deletion_at`); a request still in `recovery_required` is deleted at `watchdog_at + 24 h` all the same: occupancy is kept, data is not (A34) |

The nightly retention sweep counts and deletes overdue request-level records like overdue bookings, with the same grace (`PURGE_GRACE`) and backlog alert (`BACKLOG_AGE`). Showing them as separate counts on the retention overview is follow-up row 5d: until a writer exists (step 7) there is nothing to count. The deletion is idempotent and leaves the request, its states and its times (no personal data) for reporting.

## 4. The personal-data resolver

`contact_of(request, booking, record) -> RequestContact` in `app/domain/service_requests.py`:

- a request with a booking reads the booking's `Contact` and never the record (the adapter does not even load one for it);
- a request without a booking reads the record;
- `RequestContact.state` is `present`, `not_collected` or `deleted`; a field the flow did not collect is `None` and is never invented (no placeholder name, no empty email; A20).

`app/domain/privacy.py` gains `REQUEST_ENTITY`, `REQUEST_DATA_FIELDS` (`first_name`, `last_name`, `email`, `phone`, `form_answers`), `REQUEST_DERIVED_FIELDS` (`assigned_to`) and `ENTITIES` (`Booking`, `ServiceRequest`); `PII_FIELDS` and therefore the log mask are unchanged (the names are the same). The *Data fields* card keeps listing the fields of `DATA_FIELDS`, now naming both entities.

CLAUDE.md's privacy rule "Only on `Booking`. Nowhere else." becomes "Only on `Booking` and, for a request without an appointment, its request-level record (`request_personal_data`, ADR 0121 D1). Nowhere else." This makes PR 5a a decision PR (ADR 0078).

## 5. Error codes

| Code | HTTP | When |
|---|---|---|
| `lease_expired` | 409 | a start after the claim's lease; claim again |
| `start_refused` | 409 | `check_start` refused: the interval would take a unit reserved for someone else, or nobody is staffed now |
| `request_in_progress` | 409 | releasing a started work item |
| `request_not_started` | 409 | completing a work item that has not started |

Domain errors carry codes only; the texts are catalogue keys `error.<code>` in nl and en. Existing codes are reused where they fit (`not_claimed`, `not_eligible`, `booking_not_active`).

## 6. Settings (ADR 0042)

| Key | Class | Default | Bounds | Permission | Read |
|---|---|---|---|---|---|
| `assignment_start_lease_minutes` | D organisation | 5 | 1–30 | the registry's class D permission (`settings.organisation.manage`, held by the same roles as `capacity.manage`, as for `treatment_buffer_minutes`) | at the claim, fixed into `lease_until` |
| `request_unresolved_hours` | D organisation | 24 | 1–168 | as above | when a request is made, fixed into `watchdog_at` |

Registered in `app/domain/settings_registry.py` and `app/config.py` in PR 5b, where they are first read; `docs/settings.md` regenerated. **Fixed on purpose:** the 24 hours from the terminal state or the watchdog to the deletion (a privacy protection, ADR 0102); the watchdog round's cadence (one minute, `WATCH_EVERY`, like `AUTO_EVERY`); the closed state tables.

## 7. Admin screens (ADR 0047)

No new screen, tab or setting topic.

| What | Where | Permission |
|---|---|---|
| *Start* and *Complete* on a claimed request of a flow without video, instead of the meeting link; the recorded overrun conflict; *Complete* for a supervisor on a row in `recovery_required` | service → Agenda → Queue (exists, `wide`), the claimed item's page and its live fragment | `queue.work` (start, complete by the holder); `bookings.view` (supervisor completion), as `release` |
| The two settings | organisation → Settings (existing class D form, rendered from the registry) | the registry's class D permission |
| Request-level records holding data, overdue | the retention overview (exists) as two more counts per service: follow-up row 5d, with the first writer (step 7) | `retention.view` |

No manual topic changes (existing topics wait for the next major release; a test requires none for these elements).

## 8. Threat model rows (`docs/threat-model.md`)

| Flow | Threat | Mitigation | Test |
|---|---|---|---|
| Request record | PII outside `Booking` leaks through logs or errors | same field names as `DATA_FIELDS`, so the structlog mask and the semgrep rule cover them; domain types exclude them from `repr` | `test_request_fields_are_masked_by_the_one_list` |
| Request record | kept past its deadline | nightly sweep and backlog alert as for bookings; deletion is a row delete plus a state | `test_overdue_request_record_is_deleted_by_the_sweep` |
| Start / complete | another agent starts or completes work they did not claim | holder check inside the locked transaction; supervisors only for `recovery_required` | `test_only_the_holder_starts_and_completes` |
| Start | a late start takes a reserved unit (A35) | `check_start` read under the lock; lease expiry | `test_a_start_after_the_lease_is_refused`, `test_a_start_into_reserved_units_is_refused` |
| Watchdog | unbounded work keeps capacity or data | expiry, recovery with kept occupancy, deletion at `watchdog_at + 24 h` | `test_watchdog_*` |
| Rollback | the previous image ignores requests | tables are new; the gate keeps pool flows dark; the previous image's cancel is seen by the watchdog round | `test_a_booking_cancelled_elsewhere_cancels_its_request` |

## 9. Choices this brief made (for the product owner; built with the default)

| # | Question | Default built |
|---|---|---|
| R1 | When is the request of an appointment made? | At the first claim, inside the locked claim; the watchdog makes an `expired` one for a pool booking nobody claimed. No booking path changes |
| R2 | A separate `pool_occupancies` table, as sketched in the capacity design? | No: the occupation is four columns of the work item that holds it; one release, no join |
| R3 | What does a lease expiry do? | The claim is cleared and the request returns to the queue; the booking keeps its hold |
| R4 | Who closes work in `recovery_required`? | The holder or a supervisor (who may view the bookings) completes it; until then its unit stays held |
| R5 | Does completing set the booking to `completed`? | Yes: the operator's *Complete* is explicit evidence (A27) |
| R6 | How is the request-level record deleted? | The row is deleted and the request's state says `deleted`; nothing is set to an empty value |
| R7 | Does a booking cancelled during treatment end the work? | No: the treatment holds a unit until it is completed or the watchdog marks it for recovery |
| R8 | The flow choice screen for the participant (step 3a: "follows in step 5") | Not in step 5: follow-up row 5c; one flow per entry path keeps the demonstration |
| R9 | Where does a supervisor find work in recovery? Its booking has ended, so the queue (which shows bookings until their end) no longer lists it | The holder completes it from the claimed page, which stays open to them; a supervisor may complete it by the same action. A list of work in recovery for supervisors is follow-up row 5e |

## 10. Pull requests

| PR | Content | Acceptance |
|---|---|---|
| 5a (decision PR: CLAUDE.md and `privacy.py`) | This brief; `app/domain/service_requests.py` (states, transitions, watchdog, deletion moments, lease, resolver); `privacy.py` lists; migration 0113 and rows; `Transaction` methods and the SQL adapter `app/adapters/db/service_requests.py`; the occupation in `get_pool_commitments`; `assigned_to` in the booking purge; the fake store; CLAUDE.md privacy sentence; `docs/dataflow.md`, `docs/threat-model.md`; ADR 0121 rows 5a, 5b, 5c | A15 (one request per appointment), A20 (domain), A33, A34 (domain), the deletion list |
| 5b (build, stacked on 5a) | Claim makes the request and its work item with a lease; start, complete, release in `QueueService` and the queue pages; the watchdog round in the worker (lease expiry, cancellation, expiry, recovery, deletion of records); the nightly sweep for request-level records; settings `assignment_start_lease_minutes` and `request_unresolved_hours`; `RETENTION_BUILT = True` and the rollback point split into its own readiness check (ADR 0122 follow-up); i18n nl/en; `docs/architecture.md`, `docs/settings.md` | A19, A25, A27, A33, A34, A35 end to end with the gate opened in tests |

5b stacks on 5a: both touch the ADR's build status, `docs/adr-status.md` and the new domain module. Its pull request targets 5a's branch without auto-merge until 5a has merged and GitHub has retargeted it to `main`.
