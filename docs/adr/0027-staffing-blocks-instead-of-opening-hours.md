# 0027. Staffing blocks with an agent count, including zero, instead of opening hours

Status: accepted (2026-10-26) by the product owner, who chose labels per block, the terms
*bezetting / tijdblok / aantal medewerkers* and the full rename (phase C). Builds on HANDOVER
B5 ("capacity is a counter") and ADR 0025; related to ADR 0026 (direct-call cap).

## Context

A group's week is configured as "openingstijden": weekly windows `opens`–`closes` with a
`capacity` of 1 to 1000 (`ScheduleRule`, `ScheduleOverride`, `TimeWindow`). A gap between
windows means closed. In practice the number entered is the number of agents available for
the group's appointments in that period (HANDOVER §1: "per tijdvak een capaciteit n (aantal
beschikbare medewerkers)"), and the product owner reports that contact-centre planners think
in exactly those terms: a day consists of blocks with a number of agents, and lunch breaks
and time outside office hours are blocks with **zero** agents.

The current model works against that view:

1. **Naming.** "Openingstijden" suggests a counter that is open or closed. The planner's real
   question, "how many agents do we have from 12:30 to 13:00?", has no place in the screens
   or the code (`capacity`).
2. **Zero is not expressible.** Capacity must be at least 1 (domain and the `capacity >= 1`
   database checks). Lunch can only be modelled by leaving a gap, so the week view shows no
   difference between "lunch, deliberately nobody" and "forgotten".
3. **Slots are lost at block borders.** `availability.slots_on` starts the slot grid anew in
   every window and only offers slots that fit entirely inside one window. Two touching
   blocks with different agent counts (09:00–10:15 three agents, 10:15–12:00 two agents)
   shift the grid at 10:15, and a slot straddling the border is never offered.
4. **Changing a block** was not possible until PR #25. Splitting a block for a lunch break now
   is: change the morning block, add the afternoon block.

## Decision

1. **Concept and naming.** The week of a group is its *staffing* (NL: *bezetting*): a set of
   **staffing blocks** (NL: *tijdblokken*), each with a weekday or date, a start, an end and
   an **agent count** (NL: *aantal medewerkers*). An exception for a date (NL: *afwijkende
   bezetting*) replaces the weekly blocks of that date, as now.

   | Now (UI nl / en) | New (UI nl / en) | Code now → new |
   |---|---|---|
   | Openingstijden / Opening hours (tab) | Bezetting / Staffing | route `/hours` → `/staffing` (old path redirects) |
   | Openingstijd / opening window | Tijdblok / Block | `ScheduleRule` → `StaffingRule`, `TimeWindow` → `StaffingBlock` |
   | Capaciteit, "tot n tegelijk" | Aantal medewerkers, "n medewerkers" | `capacity` → `agents` |
   | Uitzondering / Exception | Afwijkende bezetting / Staffing exception | `ScheduleOverride` → `StaffingOverride` |
   | Gesloten / Closed | Geen bezetting / No staff | unchanged: a closed override stays `window=None` |

   `Slot.capacity_snapshot` keeps its name: a slot's capacity is *derived from* the agent
   count at that time, and a slot is not an agent.

2. **Zero agents is a valid block.** `agents` ranges from 0 to 1000. A block with 0 agents
   offers no slots and makes the group closed for direct calls during that block (`is_open`),
   exactly like a gap. It exists so the planner can say what is meant (lunch, team meeting,
   outside office hours) and see it in the week view. Gaps keep meaning "no staff", so
   existing data needs no conversion and a day does not have to be covered from 00:00 to
   24:00.

3. **Optional label per block** (max. 50 characters, no line breaks), for example "Lunch" or
   "Teamoverleg". It is admin content like a group name: shown in the admin week view only,
   never to citizens, never in logs. It carries no meaning for the scheduler.

4. **Blocks do not overlap, as now.** No layering ("lunch on top of 09:00–17:00"): one rule
   per moment keeps validation, the week view and audit simple. Lunch is modelled by splitting:
   09:00–12:00 (3), 12:00–12:30 (0, "Lunch"), 12:30–17:00 (3). The admin UI gets a **split**
   action on a block (time and agent count of the second part), so this is one form, not three.

5. **One slot grid per staffed run.** Consecutive touching blocks with agents > 0 form a *run*.
   The slot grid starts at the beginning of the run and continues across block borders. A slot's
   capacity is the **lowest** agent count among the blocks it covers. A block with 0 agents (or
   a gap) ends the run. Example above: the 10:00 slot of 30 minutes is offered with capacity 2
   instead of being dropped.

6. **Phased delivery**, each phase its own PR:
   - **A — semantics:** `agents` 0–1000, the run-based slot grid, `is_open` follows agents > 0,
     a migration relaxing the `capacity >= 1` checks to `>= 0` and adding the `label` column
     (reversible: the downgrade removes 0-blocks and the label column).
   - **B — admin UI and texts:** the new terms in `nl` and `en`, the week view with labels and
     0-blocks visibly marked (not only by colour), the split action, and `/hours` redirecting to
     `/staffing`.
   - **C — code names:** the mechanical rename in the table above, plus a migration renaming
     the tables and columns (`schedule_rules` → `staffing_rules`, `capacity` → `agents`). No
     behaviour change, so it can be reviewed as a pure rename.

## Consequences

- Planners configure what they actually plan: agents per block. Lunch and closed periods are
  visible and deliberate. Citizens see nothing new: no slots in a 0-block, just as in a gap.
- **Behaviour change for existing groups (phase A):** groups with touching windows of
  different capacity get a continuous slot grid and the slots across the border that were
  missing so far. Slots that already have bookings keep their snapshot (HANDOVER §3). Before
  the release we count affected groups (touching windows with differing slot alignment) and
  inform their admins.
- The number is still a counter for the WxCC queue (B5): we never store which agent is
  working. If agents work in more than one group (O4), the agent count per group is a share
  and the real occupancy can be lower. This ADR does not solve that.
- The direct-call cap of ADR 0026 (`direct_call_max_concurrent`) is a separate, fixed number.
  Making it follow the agent count of the current block is an obvious next step, but it
  decides how scheduled and direct calls share agents. That needs its own ADR.
- Holidays (ADR 0025) and emergency closures (ADR 0024) are unaffected: they still turn into
  closed overrides, or remove availability, before `windows_on`.
- Phase C touches many files without changing behaviour. Merging it while other PRs are open
  causes conflicts, so it is planned for a quiet moment on main.

## Choices made on acceptance

The proposal listed three open points; the product owner decided on 2026-10-26:

1. **Labels per block: yes** (decision 3), delivered in phase A (column) and B (screens).
2. **Terms: *bezetting / tijdblok / aantal medewerkers*** (en: *staffing / block / number of
   agents*), as in the table of decision 1, not the alternative "agentslots".
3. **Rename code and tables: yes** (phase C), as in the table of decision 1.
