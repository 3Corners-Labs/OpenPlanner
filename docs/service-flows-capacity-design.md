# Capacity of a shared pool: staffing and interval accounting (build brief of ADR 0122)

Status: design of ADR 0122 (step 3b of ADR 0121), all open questions decided by the product owner on 2026-11-11 (section 9). This is the build brief for the five pull requests of section 8; the decisions of section 9 override any wording above that reads as an open choice (P1 to P8). Written against the state of steps 2a to 2f.

## 0. Sources, assumptions and what I could not determine

- **The handover.** The capacity rules (the ten invariants, the 09:55 example, acceptance cases A11 to A18, A24, A35 and A36, the rollback rule and the settings list) are those of the original handover of ADR 0121; `docs/service-flows-handover.md` is its condensed form. "Invariant n" below means the n-th capacity invariant of that handover.
- **Step 3a.** ADR 0121's step 3 is one row ("one flow resolver ...; interval accounting; new-pool admission"). I assume it is split into **3a: the flow resolver** (page, widget, API, admin resolve an explicit or default flow; legacy defaults unchanged; A04, A05) and **3b: this document**. 3b depends on 3a only in its last PR (section 8). I did not find a 3a design or branch; if the resolver does not exist when 3b-5 starts, 3b-5 builds the minimum of it.
- **Open PRs.** I could not query GitHub from here. The ADR's build table marks 2b-ii and 2d as `merged` with `#PRNUM`; the task calls them open. Section 8 states dependencies either way.
- **Not measured:** query times on a realistic database; how many staffing blocks real pools will have. The complexity figures in section 2 are analytical.

## 1. Where a counted pool's staffing lives

### What exists

A service (`Group`) has weekly `ScheduleRule`s and per-date `ScheduleOverride`s, each carrying a `TimeWindow(opens, closes, capacity, label, offer, subjects)` in the service's time zone (`app/domain/scheduling.py`). `effective_schedule` (`app/domain/holiday_service.py`) adds the holidays of the chosen calendars as closed-day overrides, and an own override on a holiday date wins. Closures (`closures` table) are per service. A counted pool (`capacity_pools`, mode `counted`) has a name, a code and guaranteed skills, and no supply at all.

### Options

| | Option | For | Against |
|---|---|---|---|
| (a) | **The pool gets its own week pattern**: tables `pool_staffing_rules` and `pool_staffing_overrides`, shaped like the service's, plus a time zone on the pool | Reuses the whole pure staffing domain (`TimeWindow`, `ScheduleRule`, `ScheduleOverride`, `windows_on`, `validate_schedule`, `staffed_runs`) and the week view; planners know the screen; one home for the supply of the pool | The same person can be counted in a service's own staffing and in a pool (section 3.4); a count, not people |
| (b) | **Named roster**: the pool lists staff members with their skills and working times | Detects double counting, makes the skill guarantee checkable per unit | Is follow-up row 13 (named allocations) plus a workforce calendar (row 19); much larger; brings staff availability, which is personal data of staff, into planning |
| (c) | **Contributions**: each service's staffing page says "of my N people, K work for pool P" | Planners keep one page per service | Splits the supply of one pool over several homes (against ADR 0005); a pool edit then needs every contributing service's rights; double counting is still possible |

**Recommendation: (a).** It is the only option that is small, keeps one home, and reuses tested code. (b) stays row 13. (c) is rejected.

### Data (expand only)

| Table / column | Columns | Notes |
|---|---|---|
| `capacity_pools.timezone` | `String(64)`, nullable | Required by the domain for a counted pool (`CapacityPool.timezone`), NULL for a legacy pool. Windows are local to the pool; all accounting is UTC. A flow of a service in another zone works, because availability converts to UTC per date like `slots_on` does |
| `pool_staffing_rules` | `id`, `pool_id` (FK `capacity_pools`, `RESTRICT`), `weekday`, `opens`, `closes`, `units`, `label`, `offer`, `valid_from`, `valid_to` | Same checks as `schedule_rules` (`weekday BETWEEN 0 AND 6`, `opens < closes`, `units >= 0`, `offer IN (...)`). No `subjects` column: subjects belong to a service (ADR 0081), and a pool serves several services |
| `pool_staffing_overrides` | `id`, `pool_id`, `day`, `opens`, `closes`, `units`, `label`, `offer` | Same "window or closed" check as `schedule_overrides`; index `(pool_id, day)` |

`units` instead of `capacity`: the column means "number of staff counted at this moment" (ADR 0027 renamed the concept to *aantal medewerkers*); the domain maps it to `TimeWindow.capacity`, so all window code is reused unchanged.

`offer` reuses the stored values of ADR 0072 (`scheduled`, `direct`, `both`). On a pool they mean *appointments only*, *without appointment only*, *both*, and the pool pages label them that way (new catalogue keys `admin.pool.offer.*`). Internally `TimeWindow.offers_slots` keeps meaning "appointments may be admitted here" and `offers_direct` "on-demand work may start here". One translation table, no second window type.

**Holidays:** a pool follows the holiday calendars its organisation chose (`organisation_holiday_calendars`), applied through the existing `with_holidays`; a pool override on a holiday date wins, exactly as for a service. No own calendar choice per pool in v1 (no new table); a pool that must stay open on a holiday gets an override. This is the "inherit" path services already have.

**Domain:** new module `app/domain/pool_staffing.py`: `PoolStaffing(pool, rules, overrides)`, `check_pool_schedule` (calls `validate_schedule`, refuses subjects, refuses staffing on a legacy pool), and `supply_between(staffing, holidays, lower, upper) -> Supply` (section 2). The use cases (add, change, split, remove a block, add or remove an exception, preview) go in `app/domain/pool_planning.py`, modelled on `PlanningService` in `planning.py`, each one `AdminService.authorize(Permission.CAPACITY_MANAGE, organisation scope)`, one locked transaction, an audit event with keys only (`pool_rule.add`, `pool_override.remove`), and the shortfall report of section 2.6 on the confirmation page.

**Emergency closure (ADR 0024) on a shared pool.** A closure stays per service (ownership table of the brief; ADR 0121 point 8). It blocks *intake* of that service's flows: no slot of that service inside the closure is offered or booked, no on-demand request is accepted. It never changes the pool's supply and never releases another service's commitments. Its own confirmed appointments are suspended as today; `SUSPENDED` is in `HOLDS_PLACE`, so by default they **keep their units** in the pool until the closure ends or a planner frees them (decision P4 in section 9). Treatment already running stays counted until it ends (A24). An organisation-wide closure is a closure of every service and so of every flow on the pool.

**`max_*` and bounds.** Units per block reuse `MAX_CAPACITY = 1000` (a typo guard, fixed). No count limit on blocks per pool: services have none and `validate_schedule` is quadratic only within a weekday. `max_capacity_pools_per_organisation` (C) is unchanged.

### Screen (ADR 0047)

Home: organisation → Overview → sub-tab *Skills and capacity* (`organisation.skills_capacity`), where the pool's own page `/admin/organisations/{org}/capacity/{pool_id}` already is. New sub-pages of that screen, registered in `app/web/navigation.py` as its subpages, no new tab:

| Path | Content | Permission | Width |
|---|---|---|---|
| `/capacity/{pool_id}/staffing` | Week view of the pool's blocks (the template of the service's staffing week, fed a pool), add-block form, the double-counting notice (section 3.4), the coming shortfalls (section 2.6) | read `organisation.view`, every form and POST `capacity.manage` | wide (`wide_subpages`, like the service agenda week) |
| `/capacity/{pool_id}/staffing/{rule_id}`, `.../split` | Change or split one block | `capacity.manage` | standard |
| `/capacity/{pool_id}/exceptions`, `/exceptions/{override_id}` | Exceptions per date; holidays from the organisation's calendars listed read-only with a link to *Holidays* | `capacity.manage` | standard |

The pool's existing page gains the time zone field and a link "Staffing". A legacy pool gets none of these pages (404, as today). The help map, manual topic (at the next major release, row 9) and `docs/admin-map.md` follow the register.

## 2. Interval accounting

### 2.1 What is stored

**Appointments are not copied into a ledger; their load is derived from the bookings that already exist.** A booking of a non-legacy flow lives in `bookings` (with `flow_id`, `flow_revision`, already there since 2b) and points to a `slots` row that carries the pool:

| Column on `slots` | Meaning |
|---|---|
| `capacity_pool_id` `INTEGER NULL` (FK `capacity_pools`, `RESTRICT`) | NULL for every legacy slot; the pool for a slot of a non-legacy flow |
| `occupied_until` `DateTime NULL` | `end + buffer`, fixed at creation (the buffer in force then); NULL for legacy slots |

`kind` stays `scheduled` for a pool appointment, so `Booking.kind` keeps meaning "appointment" everywhere (it is derived from `slots.kind` in `uow.py`). The legacy unique index `uq_slots_group_start_scheduled` is recreated with `AND capacity_pool_id IS NULL`, and a new partial unique index `uq_slots_pool` on `(capacity_pool_id, group_id, start, occupied_until) WHERE capacity_pool_id IS NOT NULL` lets bookings of one service, pool, start and length share a slot row, as legacy bookings share one. Index `ix_slots_pool_start (capacity_pool_id, start)` serves the load query.

Why derive rather than keep a separate reservation table:

1. **Invariant 2 for free.** Whether a booking holds a place is `HOLDS_PLACE` (confirmed, pending confirmation, suspended) plus open rebooking proposals (ADR 0057), already computed by `places_taken()` in `app/adapters/db/proposals.py`. A second table would need a second copy of those rules and a release on every status change (cancel, expire, lapse, refuse, request rebook, purge), each a chance to leak or to free twice.
2. **Release exactly once** is automatic: a cancelled booking simply stops counting.
3. **Rollback.** An older image that cancels or moves such a booking through the participant's link changes only the booking's status, and the capacity follows. A ledger would be left stale by an image that does not know it.

**Work in treatment and on-demand work need a stored row** (they have no booking, or their occupancy outlives the plan). That row, `pool_occupancies`, belongs to the work item of step 5 and the on-demand request of step 7, not to 3b. 3b defines its domain shape (`Occupation`, section 2.2) and tests the rules with it, so step 5 adds a table and a query, not a rule. Sketch, for step 5: `id`, `pool_id`, `organisation_id`, `group_id`, `booking_id NULL` (UNIQUE: one per appointment, A15), `request_id NULL`, `state` (`assigned`, `in_progress`, `released`), `starts_at`, `planned_until`, `lease_until NULL`, `released_at NULL`. Release is a conditional `UPDATE ... WHERE state <> 'released'` (exactly once, invariant 6).

The **capacity snapshot** (CLAUDE.md) is written on the pool slot (`capacity_snapshot` = the lowest staffed units over the interval at creation), as a record for the agenda and shortfall view. Unlike a legacy slot it is **not** the ceiling for later bookings: admission always uses the pool's current staffing, because a snapshot per start cannot be compared across flows of different lengths. This changes the meaning of one CLAUDE.md sentence for pools, so it is decision P3 and a decision PR (section 8).

### 2.2 Pure domain model (`app/domain/pool_accounting.py`)

```text
Segment(start, end, units, appointments: bool, on_demand: bool)      # UTC, from the pool's windows
Supply(segments)                                                     # sorted, gaps mean 0 units
    units_at(t, intake) -> int        # 0 outside a segment, or when the segment does not offer `intake`

Commitment(key, start, until, in_progress=False)                     # key: ("booking", id) or ("request", id)
    # an appointment that holds a place:   [slot.start, slot.occupied_until)
    # an occupation (step 5/7):            [starts_at, planned_until), in_progress when started
```

`supply_between` (in `pool_staffing.py`) turns local windows into UTC segments day by day, with the same DST handling as `slots_on` (a local time that does not exist is skipped, an ambiguous one takes its first occurrence). Touching windows stay separate segments; capacity across a border is the lower of the two, as ADR 0027 already does for a slot.

```text
def free_over(supply, commitments, a, b, intake, ignore=None, open_ended=False) -> int:
    """Lowest number of free units at any moment of [a, b)."""
    live = [c for c in commitments if c.key != ignore and c.start < b and until(c) > a]
    #   until(c) = +inf when open_ended and c.in_progress and c.until <= now   (an overrun)
    points = sorted({a} | {c.start for c in live if a < c.start < b}
                        | {until(c) for c in live if a < until(c) < b}
                        | {s.start, s.end for s in supply.segments if a < s.start/s.end < b})
    lowest = +inf
    for p in points:                                   # load and supply are constant on [p, next)
        load  = count(c for c in live if c.start <= p < until(c))     # touching does not overlap
        lowest = min(lowest, supply.units_at(p, intake) - load)
    return lowest

def admit_appointment(supply, commitments, start, duration, buffer) -> Admission:
    b = start + duration + buffer
    if any moment of [start, b) has supply.units_at(_, APPOINTMENT) == 0:
        return NOT_STAFFED                      # outside staffed time, a 0-unit or walk-in-only block, a holiday
    free = free_over(supply, commitments, start, b, APPOINTMENT)
    return ADMITTED(free) if free >= 1 else FULL

def check_start(supply, commitments, now, duration, buffer, own_key=None, intake=ON_DEMAND) -> StartCheck:
    """Treatment start (step 5) and on-demand start (step 7); A17, A18, A35."""
    b = now + duration + buffer
    free = free_over(supply, commitments, now, b, intake, ignore=own_key, open_ended=True)
    return MAY_START if free >= 1 else CONFLICT(first_moment_short)

def earliest_start(supply, commitments, after, duration, buffer, limit, intake=ON_DEMAND) -> datetime | None:
    """First t in [after, limit] where check_start would pass; candidates are `after` and every
    commitment end or segment start in the range (the only moments the answer can change)."""

def shortfalls(supply, commitments, lower, upper) -> list[Shortfall(start, end, units, load)]:
    """Maximal intervals where load > units; staffing edits report them, never rewrite commitments."""
```

The rules this encodes:

- **One unit per commitment, over its whole interval** (invariant 1). Flows of different durations meet only through `[start, until)`; there is no per-start counter.
- **Reuse, no double count** (invariant 3, A15): a reservation that is checked in or started keeps its key. Its occupation in step 5 carries the same `("booking", id)` key, and the load builder keeps one commitment per key (the occupation, when there is one, else the booking's hold). `check_start(own_key=...)` leaves the request's own commitment out when testing whether it can start.
- **Appointments are protected** (invariant 5): on-demand work is admitted only through `check_start`, which counts every holding reservation. There is no switch to turn this off (switching a protection off is never C or D).
- **Overruns block conflicting starts** (invariant 7) through `open_ended=True`: an occupation still in progress past its planned end counts as busy for the whole interval being tested. It is ignored for future appointment admission (`admit_appointment` uses planned ends): a reservation is not a guarantee against overruns (invariant 8), the shortfall view shows the clash.
- **Waiting is not treatment** (invariant 4): nothing in this module counts a waiting request. Queue admission (how many may wait) is a separate limit (section 4).
- **Organisation boundary** (invariant 10): the load query filters by pool id, and a pool belongs to one organisation; the domain receives intervals only, no booking or service details.

### 2.3 The booking transaction

Inside `uow.locked()` (`BEGIN IMMEDIATE`; on SQLite this is a database-wide write lock, so every booking, move, claim and release on every pool is serialised and the re-check is simply "read inside the lock", invariant 9):

```text
book_on_pool(service_slug, flow_ref, start, contact, ...):
    async with uow.locked() as tx:
        group, revision = resolve_flow(tx, service_slug, flow_ref)       # 3a; refuses unknown/foreign/archived
        require not revision.legacy                                      # legacy goes to BookingService.book
        refuse closure of group covering start                           # group_closed
        refuse outside booking_window(group, now)                        # notice and horizon of the service
        pool  = tx.get_capacity_pool(revision.capacity_pool_id)          # counted, not archived, same org
        buffer = treatment_buffer(pool.organisation_id)                  # D setting, read now
        until = start + revision.duration + buffer
        require start on the flow's grid (section 3.1)                   # slot_unavailable
        supply = supply_between(tx.get_pool_staffing(pool), holidays, start, until)
        commitments = tx.get_pool_commitments(pool.id, start - MAX_SPAN, until)
        match admit_appointment(supply, commitments, start, revision.duration, buffer):
            NOT_STAFFED -> slot_unavailable;   FULL -> slot_full
        slot = tx.get_pool_slot(group.id, pool.id, start, until)
               or tx.add_slot(Slot(..., capacity_pool_id=pool.id, occupied_until=until,
                                  capacity_snapshot=min staffed units over [start, until)))
        address limit (ADR 0066), store the booking pending with flow_id/flow_revision, its jobs
```

`get_pool_commitments(pool_id, lower, upper)` is one query: pool slots with `start > lower - MAX_SPAN`, `start < upper`, `occupied_until > lower`, with `places_taken()` per slot (each place one commitment, keyed by booking id). `MAX_SPAN = MAX_SLOT_MINUTES + 120` (the buffer's upper bound) lets the index on `(capacity_pool_id, start)` bound the scan. Moving a pool booking (`relocate`) is the same transaction with the old booking cancelled first, as today. A rebooking proposal (ADR 0057) on a pool slot is a place like any other, through `places_taken()`.

**Complexity.** With *k* commitments overlapping the tested interval and *s* segment borders in it, `free_over` is O((k + s) log(k + s)) as written (sort the points, count by bisect on sorted starts and ends). For a day's availability (section 3) the commitments and the supply of the day are loaded once, the load becomes a step function in O(n log n), and each candidate start reads the segments it covers: O(n log n + candidates × segments per candidate). With a few hundred appointments a day per pool this is well under a millisecond in pure Python (not measured).

### 2.4 The brief's example (domain acceptance test, A17)

Pool of 2 units, block 09:00 to 12:00 offering both, buffer 0. R1 and R2 are appointments of 30 minutes at 10:00, so commitments `[10:00, 10:30)` twice. At 09:55 an on-demand request Q of 20 minutes asks to start: interval `[09:55, 10:15)`.

| Point | Units | Load | Free |
|---|---|---|---|
| 09:55 | 2 | 0 | 2 |
| 10:00 (R1, R2 start) | 2 | 2 | **0** |

`check_start` → `CONFLICT(10:00)`: treatment may not start against the reserved units. The ticket may be issued: waiting counts against queue admission, not treatment (section 4). `earliest_start(after=09:55)` tries 09:55 (fails) and 10:30 (both reservations end; free 2 over `[10:30, 10:50)`) → **10:30**, which the queue may show as the expected start. Variants the same test covers:

- Q at 09:35: `[09:35, 09:55)` has free 2 → may start. If Q then runs over to 10:05, R1 and R2 still stand; at 10:00 the load is 3 over 2 units: the shortfall view shows it, and any further start before Q ends is refused (`open_ended`).
- Buffer 5: Q at 09:40 tests `[09:40, 10:05)` → conflict at 10:00; at 09:35 it tests `[09:35, 10:00)` → may start (touching does not overlap).
- R1 checks in and starts at 10:00: `check_start(own_key=R1)` leaves R1 out, the load at 10:00 is R2 only, free 1 → may start; the load stays 2, not 3 (A15).

### 2.5 Two more edge cases

**A long appointment across a staffing gap.** Pool blocks 09:00–12:00 with 2 units, 12:00–12:30 with 0 units ("Lunch"), 12:30–17:00 with 2 units. Flow *Intake* of 90 minutes.

- Grid (section 3.1): the 0-unit block ends the run, so starts are 09:00 and 10:30 in the morning run and 12:30, 14:00, 15:30 in the afternoon run.
- A crafted request for 11:00 tests `[11:00, 12:30)`; at 12:00 units are 0 → `NOT_STAFFED` (`slot_unavailable`), whatever the load.
- If the midday block has 1 unit instead of 0, the runs touch and form one run (ADR 0027): 12:00 is on the grid and `[12:00, 13:30)` has at least 1 unit over its whole length. With a 30-minute appointment of another flow at `[12:30, 13:00)`, the free count at 12:30 is 1 − 1 = 0 → `FULL`, although at 12:00 a unit was free. A per-start counter would have sold it.

**Overlapping appointments of 15 and 30 minutes.** Pool of 2 units, 09:00–11:00, buffer 0. Flow A takes 30 minutes, flow B 15. Booked: A at 09:00 `[09:00, 09:30)` and B at 09:15 `[09:15, 09:30)`.

| Request | Interval | Points (load) | Lowest free | Result |
|---|---|---|---|---|
| A at 09:00 | `[09:00, 09:30)` | 09:00 (1), 09:15 (2) | 0 | `FULL`, although only one place is taken *at* 09:00 (A14) |
| B at 09:00 | `[09:00, 09:15)` | 09:00 (1) | 1 | admitted |
| B at 09:15 | `[09:15, 09:30)` | 09:15 (2) | 0 | `FULL` |
| A at 09:30 | `[09:30, 10:00)` | 09:30 (0) | 2 | admitted |

The availability page shows A at 09:00 as gone and B at 09:00 with 1 place left, from the same commitments.

### 2.6 Staffing drops below commitments (A36)

A pool staffing edit runs in one locked transaction: validate, write, then `shortfalls(supply_after, commitments, now, now + shortfall_horizon_days)`. Commitments are never changed or moved. The confirmation page says "this change leaves N moments with more appointments than staff" and lists them per service *with counts only*; a service outside the editor's scope is counted, not named (like `PoolFlows.hidden`). The pool staffing page lists the coming shortfalls the same way, with links to each service's agenda, where the existing planner actions of ADR 0044 (ask to rebook, cancel, accept) work per booking. New admission into a short moment is refused by the same rule (free < 1). Changing a flow's pool or duration in a new revision leaves existing bookings on their own slot rows (pool and `occupied_until` are on the slot), so their meaning is kept (A23, A36).

What 3b does **not** do: a pool-level "accept this shortfall" record (legacy has `shortfall_acceptances` per service and start). Listed as a follow-up if planners ask for it.

## 3. Availability for the participant

### 3.1 What a "slot" is for a flow

For a non-legacy flow a slot is a **candidate start on the flow's own grid**: the runs of touching pool blocks that offer appointments (`staffed_runs`, unchanged), starting at the run's start, in steps of `duration + buffer`. The step includes the buffer, otherwise two back-to-back appointments of one unit would overlap in the buffer and every second start would show as full. Every candidate is admitted with `admit_appointment`; `remaining` is the lowest free count over its interval. Flows of different lengths on one pool have different grids from the same run starts (decision P6 offers a fixed quarter-hour grid instead).

`slots_on(group, day, windows)` in `availability.py` is generalised to `grid_on(tz, length, day, windows)`, and `slots_on` becomes a one-line wrapper for the service, so legacy behaviour is byte-for-byte the same and the existing tests prove it. New function `available_flow_slots(*, revision, supply, commitments, grid_tz, buffer, first_day, last_day, now, notice, horizon) -> list[AvailableSlot]` in `pool_accounting.py` (or a sibling `pool_availability.py` if the module passes ~600 lines).

Notice and horizon come from the service (`Group.min_notice_minutes`, `horizon_days`, through `booking_window`): `FlowRevision` has no `booking_policy` yet, and the brief says to preserve the existing values. Closures of the service are applied as today (`covering`). Subjects narrow the *flow* (SubjectFlow, 3a), not the pool's blocks, which carry none.

### 3.2 What changes and what stays

| Stays exactly as is | Changes or is added |
|---|---|
| `available_slots`, `offered_slot`, `overlapping_active`, `BookingService.availability/_free_slots/_reserve/book` for a legacy revision | `BookingService` (or a new `PoolBookingService` in `app/domain/pool_booking.py`, preferred so `services.py` does not pass 1,130 lines further) handles a non-legacy revision: availability, book, move, cancel |
| `schedule_rules`, `schedule_overrides`, service holidays, `direct_call_max_concurrent`, `direct_queue.py` | New tables of section 1, the two `slots` columns and the two indexes of section 2.1 |
| Legacy slot queries in `uow.py`, `proposals.py`, `usage_slots.py`, `usage_rollup.py`, `retention.py`, `rotation.py`, `config_usage.py`, `health.py` keep their logic | Each legacy query of a service's scheduled slots gains `capacity_pool_id IS NULL`, through **one** SQL predicate `legacy_slot()` in `app/adapters/db/` so no query forgets it; a test seeds a pool slot at the same service and start as a legacy slot and runs availability, booking, agenda, shortfall, usage close and the direct queue, asserting the legacy answers do not move |
| The public routes `/g/{slug}`, the widget and `/api/v1` availability shape (`start`, `end`, `remaining`) | Their availability call goes through the 3a resolver; a request without a flow resolves the default (legacy) flow and nothing changes |

Reporting (`slot_close.py`, `usage.py`) skips pool slots in 3b: their completion is defined in step 5. Pool slots carry no personal data, so `privacy.py` and the purge are unchanged.

### 3.3 Legacy and new on one service

A service keeps its legacy appointment flow on its own staffing and may get new flows on a pool. Both are bookable at the same time, on different supplies. The accounting never mixes them, and that is correct as long as the people are different.

### 3.4 The same person counted twice

The software cannot see people, only counts, so it cannot prevent one person from being counted in a service's own staffing and in a pool, or in two pools (the brief and follow-up row 13 say so). What the proposal does:

1. **Say it where the number is typed.** On the pool staffing page and the flow editor's capacity section, a fixed notice: "Een pool telt mensen, geen namen. Telt u iemand hier en ook in de bezetting van een dienst of in een andere pool, dan rekent OpenPlanner met twee mensen waar er één is." / "A pool counts people, not names. If you count someone here and also in a service's own staffing or in another pool, OpenPlanner plans for two people where there is one." (The existing count warning of screen 4 is extended, not doubled.)
2. **Show the overlap it can see.** The pool staffing page lists, per weekday, the services of the organisation that have a flow on this pool *and* own staffing blocks overlapping the pool's blocks in time ("Service X also has its own staffing on Tuesday 09:00–12:00"). Pure computation over data the editor may read; services outside the editor's scope are counted, not named.
3. **Nothing blocks it.** Whether to block it is decision P5.

## 4. On-demand requests and admission

Three separate limits, never one timeout (brief §10):

| Limit | What it protects | Where | Built in |
|---|---|---|---|
| **Queue admission** | How many may wait for a pool at once (a ticket, a waiting row), invariant 4 | A pool field `max_waiting` (NULL = no limit, 1 to 1000), added with its table in step 7; plus a look-ahead: a ticket is issued only if `earliest_start` finds a fit within `on_demand_admission_lookahead_minutes`, so nobody is admitted to a queue that cannot be served today | step 7 (domain function in 3b) |
| **Treatment start** | Reserved appointments and overruns, invariants 5 and 7 | `check_start` under the lock at claim and again at treatment start (A35) | rule in 3b; wired in steps 5 and 7 |
| **Assignment lease** | A claimed but unstarted request does not hold a unit forever, invariant 6 | `pool_occupancies.state = 'assigned'`, `lease_until = now + assignment_start_lease_minutes`; expiry by a conditional update; an expired assignment cannot be started, it must be claimed again (`check_start` once more) | step 5 |

The policy "immediate work may start only when its expected treatment interval plus buffer fits without consuming reserved capacity" is exactly `check_start(now, duration, buffer, intake=ON_DEMAND) == MAY_START`. For an appointment's own start the intake is `APPOINTMENT` and `own_key` is its booking. If an overrun leaves no unit free when an appointment is due, `check_start` returns `CONFLICT`; whether the operator may still start it (with the conflict recorded) or must wait is decision P8.

An assignment acquires the unit: when an operator claims on-demand work, the occupation row (`assigned`) is written in the same locked transaction as the claim, so a competing claim or a booking that would need that unit sees it (invariant 4). Claiming an *appointment* writes no new commitment (the booking's hold is it); when its lease expires the appointment goes back to waiting and its hold stays. Lease expiry never touches an `in_progress` occupation.

**The legacy direct call stays as it is.** `direct_queue.has_room` caps waiting plus connected direct calls per service (`direct_call_max_concurrent`, ADR 0026); a legacy direct call takes no slot capacity, and a legacy `both` block counts the same people for slots and for direct calls on purpose. None of that touches a pool. The legacy `direct` flow is the wrapper of that path and keeps it.

**Split over steps:**

| Step | Part |
|---|---|
| 3b | Pool staffing; the pure rules `admit_appointment`, `check_start`, `earliest_start`, `shortfalls` with the A17/A18/A35 domain tests; the appointment booking path (dark); the readers that keep pool slots out of legacy paths |
| 5 | `pool_occupancies` table, work item, claim with lease, start (`check_start`), complete and release exactly once, the overrun shown in the queue (screen 6's reservation notice, finding 4), recovery keeping the occupancy (A34), settings `assignment_start_lease_minutes`, `request_unresolved_hours` |
| 7 | On-demand intake (operator and device), tickets with idempotency (A16), `max_waiting` and the look-ahead, check-in reusing the reservation (A15 end to end) |

## 5. Compatibility and rollback (ADR 0116)

### 5.1 Migration (one, expand only; number checked against `origin/main` and open PRs when the PR opens, 0111 at the time of writing)

| Change | Kind | Old image on the expanded database |
|---|---|---|
| `capacity_pools.timezone` nullable | add column | ignores it |
| `pool_staffing_rules`, `pool_staffing_overrides` | new tables | does not read them |
| `slots.capacity_pool_id` (nullable FK), `slots.occupied_until` (nullable) | add columns (plain `ALTER TABLE ADD COLUMN`, no rebuild) | ignores them while no pool slot exists |
| `uq_slots_group_start_scheduled` recreated with `AND capacity_pool_id IS NULL`; new `uq_slots_pool`, `ix_slots_pool_start` | index drop and create | the recreated index is looser, never tighter |

Nothing is dropped, renamed or tightened. Downgrade drops the tables, columns and new indexes and restores the old index; it **refuses** while a pool slot exists (the precedent is migration 0010 for direct-call slots), because restoring the old unique index could fail and the bookings would lose their pool. No backfill: legacy pools get no staffing, counted pools start empty.

Rejected alternative: a new slot kind `pool`. It makes an old image fail loudly instead of quietly, but it needs a table rebuild of `slots` to widen the `kind` check, and it would give `Booking.kind` (derived from `slots.kind`) a third value that most of the code reads as "not scheduled", which is wrong for an appointment.

### 5.2 What the previous image does

The previous supported image **N-1** knows neither pool staffing nor pool slots. If a pool slot existed, N-1 would count its bookings in the service's legacy slot at the same start (`get_slot_usage` filters on group and kind only) and `get_slot` could meet two rows: it would under-sell or fail, and it would never respect the pool across services. So N-1 must never meet a pool slot. That holds as long as no non-legacy flow is active, because only an active non-legacy flow writes one.

**Proposal: build dark, activate one minor later.**

1. Step 3b ships in minor **N** dark: nothing can activate a non-legacy flow (`RETENTION_BUILT` is false until step 5, and the new gate below). N contains every reader (`legacy_slot()`), the pool accounting and the booking path. Rolling back from N to N-1 is safe because no pool slot exists.
2. A non-legacy flow may be activated only in an image whose **previous supported minor already contains the readers and the accounting** of every record it writes (pool slots from 3b, occupancies from step 5, requests from step 7). Then a rollback lands on an image that *accounts for* the obligations, the first of the two options the brief allows. Rolling back N+1 to N: N books and moves pool appointments under the same lock with the same rule, and ignores occupancies only if they are newer than N, which point 3 excludes.
3. The gate is a constant in `app/domain/service_flows.py`, next to the five `BUILT_*` constants: `ROLLBACK_SAFE = False`, with its docstring naming the oldest minor whose readers the activation relies on. `can_activate` refuses while it is false, with the page sentence that already exists ("new flows cannot be activated in this version yet"). It is set to true in the release *after* the last reader-adding step, and the upgrade matrix (`scripts/upgrade-matrix.sh`, `upgrade_fixture.py`) proves it: run the new image, activate a flow, book on the pool, start (step 5), roll back to the previous minor, book and claim there, and check that no unit was sold twice (A30). Until that run is green the constant stays false.

The restriction alternative (an older image that refuses intake for affected services) is not needed and is weaker: it would close services rather than serve them.

**Consequence the product owner should know:** the first non-legacy flow can go live at the earliest one minor release after the last step it depends on (step 5 for appointments without video, step 7 for on-demand). ADR 0121 point 9 already requires the rollback proof; this makes the timing explicit.

### 5.3 What 3b adds to the configuration package

Pool time zone and staffing (rules and exceptions) are configuration and must travel with export and import (A29), in `app/domain/configuration_v1_skills.py`'s pool section (or a sibling) and `app/adapters/db/config_flows.py`, versioned like the 2b-ii additions. Pool slots and bookings are not configuration and are not exported.

## 6. Settings

| Key | Class | Default | Bounds | Permission | Step | Why this class |
|---|---|---|---|---|---|---|
| `treatment_buffer_minutes` | D organisation | 0 | 0–120 | `capacity.manage` | **3b** | A working practice of the organisation (wrap-up time); not a protection; not needed before login. Read at admission and fixed into `occupied_until`, so a later change does not move existing commitments. One value per organisation as ADR 0121 lists it; a per-flow buffer is a follow-up if one organisation runs flows with different wrap-up times |
| `on_demand_admission_lookahead_minutes` | D organisation | 120 | 15–480 | `capacity.manage` | 7 (rule in 3b) | How far ahead a ticket must have a realistic start; a service-level judgement |
| `assignment_start_lease_minutes` | D organisation | 5 | 1–30 | `capacity.manage` | 5 | As ADR 0121 |
| `request_unresolved_hours` | D organisation | 24 | 1–168 | `capacity.manage` | 5 | As ADR 0121 |
| `shortfall_horizon_days` (existing) | D organisation | 14 | 1–60 | `settings.organisation.manage` | 3b reuses | Same meaning (how far ahead shortfalls are listed), so reused, not duplicated |

Pool data that is configuration of one object, not a setting (like a service's `direct_call_max_concurrent`): `capacity_pools.timezone` (3b), `capacity_pools.max_waiting` (7). The registry lists settings only; the pool form validates these.

Fixed, and the PR says so: appointment protection against on-demand work (a protection, never C or D); units per block 0 to `MAX_CAPACITY` (typo guard); 5-minute grain and 5 to 480 minutes per appointment (`valid_slot_minutes`, shared with services); "touching intervals do not overlap"; the grid step = duration + buffer (unless P6 decides otherwise); `MAX_SPAN` (derived from two bounds).

## 7. Acceptance and tests

| ID | Proved in 3b? | How |
|---|---|---|
| A11 two flows share a pool of 2 | **yes** | Domain: two flows' commitments, a third admission refused at every moment of overlap. SQL (`tests/adapters/db/`): two services' flows on one pool, `asyncio.gather` of several `book_on_pool` calls on separate connections for the last unit; exactly one succeeds, the rest get `slot_full` (the pattern of the existing concurrent tests in `tests/adapters/db/test_uow.py`) |
| A12 independent pools | **yes** | Domain and SQL: a full pool P1 leaves P2's availability unchanged |
| A13 X and Y on two people | already (2a, `guarantees`); 3b adds | A pool unit counts once whatever skills it holds: a flow requiring X and a flow requiring Y on one pool of 1 unit cannot both be booked at one moment |
| A14 different durations | **yes** | The 15/30 table of 2.5 as a domain test; the gap case; a randomized check against a brute-force minute-by-minute counter (seeded `random`, no new dependency) for a few thousand generated days |
| A15 double check-in | partly | Domain: `own_key` reuse keeps the load at 2 (2.4). End to end in step 7 |
| A16 ticket retried | no | Step 7 |
| A17 immediate before reserved | **yes (domain)** | The brief's example, verbatim, plus the buffer and 09:35 variants |
| A18 overrun | **yes (domain)** | `open_ended` blocks a start during the overrun; `shortfalls` reports it; future admission ignores it. Visible in the queue in step 5 |
| A24 closure on a shared pool | partly | Domain and SQL: closing service A refuses A's intake, keeps A's suspended holds (per P4) and does not change B's availability apart from those holds. Active treatment in step 5 |
| A35 late start | **yes (domain)** | `check_start` at a later `now` with an overrun or a new booking in between refuses; lease expiry in step 5 |
| A36 staffing or binding change | **yes** | A staffing reduction keeps all bookings and lists shortfalls; new admission refused there; a new revision with another pool or duration leaves old bookings on their slot rows; removing a still-required guaranteed skill stays refused (2c) |
| A03 legacy unchanged | **yes (regression)** | The pool-slot-next-to-legacy-slot test of 3.2; the whole existing booking suite unchanged |

**Mutation testing** (`mutmut` on `app/domain/`, ≥ 80 %) on `pool_accounting.py` should be killed by: `<` vs `<=` at interval ends (the touching cases in 2.4 and 2.5), `>= 1` vs `> 1` in admission (last unit tests), dropping the segment borders from the points (the gap case), dropping commitment ends from the points (an interval that sees a commitment end and another begin), `ignore=own_key` removed (check-in reuse), `open_ended` ignored (overrun), the step `duration` instead of `duration + buffer` (the buffer grid test), `min` vs `max` across touching blocks (ADR 0027 case). The brute-force comparison kills most arithmetic mutants at once.

## 8. Build plan

| PR | Scope | Files | Demonstrable | Depends on | Decision PR? |
|---|---|---|---|---|---|
| **3b-1** ADR and domain | ADR 0121 amendment (point 6 detailed: derived appointment load, live staffing for pools, the gate; *Settings* gains `treatment_buffer_minutes` as built and the look-ahead; build table rows 3a/3b split); CLAUDE.md sentence on the capacity snapshot (P3); `pool_staffing.py`, `pool_accounting.py`, `grid_on` refactor in `availability.py`; `ROLLBACK_SAFE` constant | `app/domain/`, `tests/domain/`, `docs/adr/0121-...md`, `CLAUDE.md`, `docs/adr-status.md` | Domain tests A11, A12, A14, A17, A18, A35, A36; mutation score | none (domain only) | **Yes**: CLAUDE.md and an ADR outside its build table → `po:approved` (ADR 0119: the design merges with its first build step, which this is) |
| **3b-2** Schema and adapter | Migration (section 5.1), rows, `Transaction` methods (`get_pool_staffing`, `save_pool_rule`, ..., `get_pool_commitments`, `get_pool_slot`), `legacy_slot()` predicate in every legacy slot query, the "pool slot beside legacy slot" regression test, upgrade fixture seeds pool staffing | `migrations/versions/`, `app/adapters/db/models.py`, new `app/adapters/db/pool_staffing.py`, `uow.py`, `proposals.py`, `usage_*`, `retention.py`, `rotation.py`, `upgrade_fixture.py`, `app/domain/ports.py` | Migration up/down on a fresh database; upgrade matrix green; legacy suite unchanged | 2f (migration 0110), 3b-1 | no |
| **3b-3** Configuration package | Pool time zone and staffing in export and import, versioned | `app/domain/configuration_v1_*.py`, `app/adapters/db/config_flows.py`, `docs/configuration-document.md`, schema files | Round trip keeps staffing (A29) | 2b-ii, 3b-2 | no |
| **3b-4** Pool staffing screens | Use cases in `pool_planning.py` (authorize `capacity.manage`, audit), routes in `app/admin/capacity_routes.py` (or a new `pool_staffing_routes.py` if that file grows past ~400 lines), templates reusing the staffing week partials, navigation subpages, catalogue keys nl and en, the double-counting notice, the shortfall list, `e2e/pages.json` entries | `app/admin/`, `app/web/navigation.py`, `app/i18n/locales/`, `docs/admin-map.md`, `tests/admin/` | A planner of the organisation sets a pool's week and an exception, sees shortfalls; keyboard, scripts off, 320 px (A32); label `ci:full` | 2c, 2d, 3b-2 | no (uses `capacity.manage` as is; yes only if P2 chooses a new permission) |
| **3b-5** Booking on a pool (dark) | `PoolBookingService` availability, book, move, cancel for a non-legacy revision behind the 3a resolver; readiness gains reason `pool_not_staffed`; legacy paths refuse to move a booking across models (`relocate` of a non-legacy booking into the legacy schedule, and the reverse); SQL concurrency test A11 | `app/domain/pool_booking.py`, `services.py` (dispatch only), `service_flows.py` (readiness), `app/api/` only via the resolver | Tests only: nothing in production can reach it (no active non-legacy flow) | 3a, 3b-2 | no |

Order: 3b-1 → 3b-2 → (3b-3, 3b-4, 3b-5 in parallel). Five PRs of a few hundred lines each plus tests; 3b-2 is the riskiest to review because it touches many legacy queries. Claim the migration number by opening 3b-2 early.

### Risks

1. **A legacy slot query that forgets `capacity_pool_id IS NULL`** quietly counts pool bookings in a service's legacy capacity. Mitigation: one predicate, the side-by-side regression test, and a test that fails when a new query selects `SlotRow` with `kind == scheduled` without the predicate (an AST or SQL-compile check in `tests/adapters/db/`).
2. **Rollback**: activating before the previous minor has the readers would let an old image under-sell or crash. Mitigation: `ROLLBACK_SAFE` false until the upgrade matrix proves A30.
3. **Time zones**: a pool in one zone serving a service in another; DST days. Mitigation: UTC segments built per local date with the existing rule; tests on the two DST Sundays with a 90-minute appointment crossing 02:00–03:00.
4. **Double counting by administrators** cannot be prevented (section 3.4); the notice and overlap list reduce it, row 13 solves it.
5. **Lock contention**: every pool booking holds SQLite's single write lock while it loads a few hundred rows. Fine at current volumes; measure in the e2e stack before activation.
6. **Shortfall noise**: a pool shared by many services may show many shortfalls after one edit; counts only, grouped per day, with the 14-day horizon.
7. **Scope creep into step 5**: overruns, leases and the queue notice need work items. Keep 3b to rules plus appointment booking; resist adding `pool_occupancies` before its writer exists.

## 9. Decided 2026-11-11

Plain terms: a *pool* is a shared group of staff, counted as a number. A *unit* is one person in that count. A *block* is a time period in the week with a number of staff, as on today's *Bezetting* tab. The product owner accepted every recommendation; the question and the options stay below so a reader sees what was weighed.

**P1. Where the staff numbers of a shared pool are entered.** Options: (a) the pool gets its own weekly schedule and exceptions, the same screen as a service's staffing today; (b) the pool lists named staff with their hours; (c) each service says how many of its own people work for the pool. **Decided: (a).** Small, reuses existing screens and code. (b) stays the later "named allocations" item (row 13); (c) spreads one number over several pages and still allows double counting.

**P2. Who may change a shared pool's staffing.** Options: (a) only who holds `capacity.manage` (the organisation admin by default); (b) a new right that can be given to planners. **Decided: (a) now, (b) when planners ask.** (b) changes the rights matrix (a decision PR), and a planner of one service could reduce capacity that other services rely on.

**P3. When staff numbers go down after appointments were made.** Options: (a) new bookings stop at once where there are no longer enough staff; existing appointments stay and are shown as a shortage; (b) as services work today: a time with a first booking keeps selling up to the staff count of that first booking. **Decided: (a) for pools; services keep (b).** Consequence: the pool's capacity is read live from its staffing, not copied; one sentence of CLAUDE.md about the capacity snapshot gains a pool clause (this decision PR).

**P4. One service closes in an emergency while it shares a pool.** Options: (a) its suspended appointments keep their place in the pool; (b) the places are freed at once for the other service. **Decided: (a),** plus the existing planner action "ask to rebook", which frees a place on purpose. With (b) a reinstatement could fail and a participant told "your appointment is on hold" could lose it.

**P5. The same person counted in a service's own staffing and in a pool (or two pools).** Options: (a) warn; (b) block; (c) a one-time "move this service to the pool" operation. **Decided: (a) now; (c) as a later step.** (b) is wrong for services whose own staff really are different people; the system cannot tell.

**P6. Which start times a participant sees for a flow on a pool.** Options: (a) one after another from the start of each block, every appointment length plus wrap-up; (b) a fixed grid, for example every 15 minutes. **Decided: (a).** (b) leaves gaps nobody can book and makes the page longer.

**P7. Keeping room for people without an appointment.** Options: (a) per block, the existing offer field: appointments only, without appointment only, or both; (b) per block a number of staff kept free for people without an appointment. **Decided: (a).** It reuses a known field; (b) can be added later as an extra number on the block.

**P8. An appointment is due, but a colleague's previous conversation runs over and no one is counted free.** Options: (a) the appointment may still start and the conflict is recorded and shown; (b) the start is refused until a unit is free. **Decided: (a) for an appointment starting at its own time, (b) for people without an appointment and for early starts.** With (a) the count can show more people busy than there are for a few minutes, and the shortage is visible to the planner; with (b) the system can refuse a start while a colleague is actually free, because it counts, it does not see.

### Where the code of pull request 3b-1 follows or refines the sections above

- The staffed segments and the intake enum live in `app/domain/pool_staffing.py` (with `supply_between`), the commitment rules in `app/domain/pool_accounting.py`; section 2.2 listed both under accounting.
- The overrun is passed as `overrun_at` (the instant at which a started commitment past its planned end counts as busy to the end of the interval) instead of a boolean `open_ended`.
- Only the moments where free units can drop are measured (the start of the interval, commitment starts, staffing borders); a commitment's end only frees a unit.
- `check_start` also answers P8: a commitment of its own that has begun and not ended makes the appointment due, and the outcome is `OVERRUN_CONFLICT` instead of a refusal.
- A shortage counts the people staffed whatever the block offers; admission and starts count only the units of the block's offer.
- `grid_on(tz, length, day, windows)` takes one length (the step and the slot length); a flow on a pool passes duration plus buffer.
- `ROLLBACK_SAFE` exists (section 5.2) and is not yet read by `readiness`; step 3b-5 adds the reason that reads it.
- Pull request 3b-1 is the new ADR 0122 together with the domain code, not an amendment of ADR 0121 (ADR 0121 keeps its text; its step 3 row points here).
