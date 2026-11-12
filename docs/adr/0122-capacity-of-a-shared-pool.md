# 0122. Capacity of a shared pool: staffing and interval accounting

Status: accepted, partly built (2026-11-11), requested by the product owner. Extends ADR 0121 (step 3b); builds on ADR 0027 (staffing blocks), ADR 0072 (what a block offers), ADR 0116 (expand/contract) and ADR 0042 (settings).

The full design, with the algorithms, the worked examples and the plan of five pull requests, is [`docs/service-flows-capacity-design.md`](../service-flows-capacity-design.md); it is the build brief of this ADR, and [`docs/service-flows-handover.md`](../service-flows-handover.md) is the condensed brief of ADR 0121. The decisions below are the testable summary.

## Context

ADR 0121 lets several flows, of several services, draw on one capacity pool, so that a team's people are counted once. A pool of mode *counted* has a name, a code and guaranteed skills, and no supply at all: nothing says how many people it has, when, or how appointments of different lengths share them. A service's slots cannot be reused for that: they snapshot a capacity per start time, and appointments of 15 and 30 minutes on one pool do not share a start time. Without staffing and interval accounting a pool either oversells (two flows book the same person) or sells nothing.

## Decision

1. **A counted pool has its own weekly blocks and exceptions** (P1), shaped like a service's staffing (`ScheduleRule`, `ScheduleOverride`, `TimeWindow`, where `capacity` means *units*: people counted), in a time zone of the pool. All accounting is UTC; blocks become UTC segments per local date with the daylight saving rule of `availability.slots_on` for a start; a block that ends in the missing hour of the spring change ends at the change, so segments never overlap. A block carries no subjects. Holidays follow the organisation's calendars; an own exception wins.
2. **Only `capacity.manage` changes a pool's staffing** (P2). A new right for planners comes when planners ask, as a decision PR.
3. **A pool's capacity is its live staffing, not a copy** (P3). When staffing drops below the commitments, nothing is rewritten, moved or cancelled; new admission is refused over every moment that is short, and the shortage is listed with counts (and per service for a reader who may see it). A service's own slots keep the capacity snapshot of CLAUDE.md.
4. **Appointment load is derived, not copied.** A pool appointment is a booking on a slot that carries the pool and its occupied end (`start + duration + buffer`, fixed at creation); a booking holds a unit while it holds a place (confirmed, pending confirmation, suspended, open rebooking proposal). There is no second ledger that could leak or free a unit twice, and an older image that cancels a booking releases its unit.
5. **Interval accounting.** A commitment is one unit over `[start, until)`; touching intervals do not overlap. A candidate is admitted when, over its whole interval, the staffed units minus the commitments is at least 1 at the busiest moment. The result is "not staffed" when any moment has no unit that takes appointments, and "full" otherwise when no unit is free. Appointments of different lengths therefore never overbook a pool.
6. **An emergency closure of one service keeps that service's suspended appointments in the pool** (P4), until the closure ends or a planner frees them (ADR 0044 "ask to rebook"). It never changes the supply and never releases another service's appointments.
7. **Double counting is warned about, not blocked** (P5): a notice where the numbers are typed, and a list of the overlaps the system can see. The system counts people, it does not know them; named allocations are a follow-up of ADR 0121.
8. **Start times for a flow on a pool** (P6): one after another from the start of each run of touching blocks, in steps of the appointment length plus the wrap-up (`treatment_buffer_minutes`), while a whole step fits before the run closes.
9. **A block offers appointments only, work without appointment only, or both** (P7), with the existing offer field of ADR 0072. Appointments are never admitted in a block for work without appointment, and the reverse.
10. **Work without an appointment never consumes reserved capacity.** It may start only if its expected interval plus the wrap-up fits at every moment without taking a unit that a reservation or a running overrun holds; a ticket may still be issued while it waits, and the earliest start is computed. A running commitment past its planned end counts as busy to the end of the tested interval, and stays visible as a shortage.
11. **An appointment that is due may start during a colleague's overrun** (P8). Due means its own commitment has begun and not ended; `check_start` then needs the intake `appointment` (it has no default). It starts with a recorded conflict only when someone is staffed for appointments at the moment of the start and no unit is free at that moment, because colleagues' commitments (an overrun among them) hold them all. When a unit is free now but the interval runs into reserved work later (a late arrival into the next reservation), it may start and the outcome reports the later shortage; the next reservation is never displaced, and its own start is the one that is due then. Starts without appointment, early starts, starts after the appointment's own interval and starts where nobody is staffed any more (staffing dropped) stay refused. The check is repeated under the lock at the moment of the start.
12. **The rollback gate `ROLLBACK_SAFE`** (`app/domain/service_flows.py`) stays `False`. A non-legacy flow writes pool slots that the previous supported minor would count in a service's legacy slot; so a flow may be activated only in an image whose previous supported minor already holds the readers and the accounting of every record it writes, proven by the upgrade matrix (A30). The constant is wired into `readiness` in step 5, next to the booking path; until it is true no non-legacy flow can be activated, and a rollback is safe. Until step 5 nothing can create a non-legacy flow at all: only the pure domain code exists, no table, route or screen reaches it.
13. **Five pull requests** (design section 8): step 1, this ADR with the pure domain code (`app/domain/pool_staffing.py`, `app/domain/pool_accounting.py`, the `grid_on` refactor of `app/domain/availability.py`, `ROLLBACK_SAFE`); step 2, schema and adapter (migration, the `legacy_slot()` predicate in every legacy slot query, the pool-slot-beside-legacy regression test); step 3, the configuration package; step 4, the staffing screens under organisation, Overview, *Skills and capacity*; step 5, booking on a pool, dark (nothing in production can reach it).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `treatment_buffer_minutes` | D organisation | 0 | 0–120 | `settings.organisation.manage` | Wrap-up time added to every appointment of a pool. Read at admission and fixed into the commitment's end, so a later change does not move existing commitments. One value per organisation, not per pool or flow. Registered in step 2 (`MAX_BUFFER_MINUTES` is derived from the registry bound); first read in step 5 (booking on a pool). The registry derives the permission of every class D setting; it is held by the same roles as `capacity.manage` |
| `on_demand_admission_lookahead_minutes` | D organisation | 120 | 15–480 | `capacity.manage` | How far ahead a ticket must have a realistic start. Planned: step 7 of ADR 0121 (the rule `earliest_start` exists in step 1) |
| `assignment_start_lease_minutes` | D organisation | 5 | 1–30 | `capacity.manage` | As ADR 0121. Planned: its step 5 |
| `request_unresolved_hours` | D organisation | 24 | 1–168 | `capacity.manage` | As ADR 0121. Planned: its step 5 |
| `shortfall_horizon_days` (existing) | D organisation | 14 | 1–60 | `settings.organisation.manage` | Reused for how far ahead shortfalls are listed |

Configuration of one object, not a setting (like a service's `direct_call_max_concurrent`): the pool's time zone (step 2) and, with ADR 0121 step 7, `max_waiting`.

**Fixed on purpose:** the protection of appointments against work without appointment (a protection is never class C or D); units per block 0 to `MAX_CAPACITY` (a typo guard); the 5-minute grain and the 5 to 480 minutes per appointment (`valid_slot_minutes`, shared with services); touching intervals do not overlap; the grid step is length plus buffer; `MAX_SPAN` (480 plus 120 minutes, derived from two bounds); `MAX_BUFFER_MINUTES` (the bound of the setting).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR and build brief (`docs/service-flows-capacity-design.md`) | #746 | merged |
| 1 | Pure domain: pool staffing, interval accounting, the grid, the rollback gate constant, the sentence in CLAUDE.md | #746 | merged |
| 2 | Schema and adapter: migration, rows, `Transaction` methods, `legacy_slot()` predicate, regression test | #754 | merged |
| 2 | Settings: `treatment_buffer_minutes` in `app/domain/settings_registry.py`; `MAX_BUFFER_MINUTES` moves there or is derived from it, with a test that the two agree | #754 | merged |
| 3 | Pool time zone and staffing in the configuration package | | planned |
| 4 | Pool staffing screens: time zone, weekly blocks, exceptions, shortfall list, double-counting notice | #760 | merged |
| 4 | Double-counting overlap list and the shortfall per service for a reader who may see it | | planned |
| 5 | Booking on a pool (dark), the reason that reads `ROLLBACK_SAFE`, concurrency test | | planned |
| 5 | Closing and reporting of pool bookings: `ended_slots`, usage closing and the facts skip pool slots until then | | planned |

## Decisions of the product owner (2026-11-11)

All eight open questions of the design were answered with the recommendation:

| # | Question | Decision |
|---|---|---|
| P1 | Where a pool's staff numbers are entered | The pool's own weekly schedule and exceptions |
| P2 | Who may change a pool's staffing | `capacity.manage` only; a planner right later |
| P3 | Staff numbers go down after appointments exist | New bookings stop at once, existing appointments stay as a shortage; pools read live staffing |
| P4 | One service closes while sharing a pool | Its suspended appointments keep their places |
| P5 | The same person counted twice | Warn only; a "move this service to the pool" operation later |
| P6 | Start times for a flow on a pool | One after another from each block start, length plus wrap-up |
| P7 | Room for people without an appointment | Per block: appointments only, without appointment only, or both |
| P8 | An appointment is due while a colleague overruns | It may start and the conflict is recorded; other starts are refused |

## Consequences

- CLAUDE.md gains one sentence, marked "(ADR 0122, from step 5)" because only pure domain code exists until then (a counted pool's staffing is read live and appointments are accounted over intervals; a service's slots keep the snapshot), so this is a decision PR.
- Step 1 changes no behaviour: the legacy slot calculation is `grid_on` called with its present arguments, proven equal to the old function on a spread of inputs; no table, port, setting or screen is added.
- A non-legacy flow can go live at the earliest one minor release after the last step it depends on, because of the rollback gate.
- `docs/architecture.md` names the two new modules in its module map; the data flow and the threat model are unchanged until step 2 (no new personal data: pool slots carry none).
