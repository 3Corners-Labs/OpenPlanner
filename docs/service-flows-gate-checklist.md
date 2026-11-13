# Before the rollback gate opens: the checklist of ADR 0121 and ADR 0122

`ROLLBACK_SAFE` in `app/domain/service_flows.py` is `False`. While it is, no non-legacy flow can
be activated, named or reached, and a rollback to the previous image is safe (ADR 0122 decision
12). This list is everything that must be done before a pull request may set it to `True`, built
from the open rows of the two ADRs' *Build status* tables and the findings of the compatibility
proof ([`service-flows-compatibility.md`](service-flows-compatibility.md), ADR 0121 step 8).

Opening the gate is a decision PR (it changes the behaviour of an accepted ADR and the sentence
in CLAUDE.md), merged only with `po:approved`, and only in an image whose previous supported minor
already holds the readers and the accounting of every record a non-legacy flow writes (ADR 0122,
consequences): at the earliest the first minor after 0.6.0.

`tests/test_service_flows_compatibility.py` reads this table: every open row of either ADR has an
item here, and `ROLLBACK_SAFE` must be `False` while any item with *Blocks* `yes` is not `done`.

Columns. *Source*: `0121/<step>` or `0122/<step>` is a row of that ADR's *Build status* table,
`step 8` a finding of the compatibility proof. *Blocks*: `yes` must be done before the gate
opens; `cc` must be done before a flow that dispatches to the contact center is activated (the
gate may open for own-queue flows first); `no` does not hold the gate. *Owner*: `build` (the
build train), `po` (the product owner's decision), `release` (whoever cuts the release),
`operator` (a check on the test host). *Status*: `open`, `planned`, `follow-up` or `done`, with
the PR that did it.

| Item | What | Source | Blocks | Owner | Status |
|---|---|---|---|---|---|
| G01 | Interval accounting across the flows of one pool and staffing of counted pools: built by ADR 0122 steps 1 to 5; the row of ADR 0121 is closed against ADR 0122 once G03 is done (assumption of step 8: nothing else is meant by it) | 0121/3b | yes | build | done (#807) |
| G02 | The double-counting overlap list and the shortfall per service for a reader who may see it (the notice where numbers are typed exists) | 0122/4 | no | build | planned |
| G03 | Closing and reporting of pool bookings: `ended_slots`, usage closing and the facts skip pool slots until then, so a pool booking would never close. Built: a pool slot is closed by the pool rule of `app/domain/slot_close.py` | 0122/5 | yes | build | done (#807) |
| G04 | The agent API's change-request check judges a requested time on the pool of a matched pool booking, without telling the caller whether anything matched | 0122/5 | yes | build | done (#808) |
| G05 | A list of work in recovery for supervisors, and an alert while any exists (design R9). Built: *Work in recovery* on the queue page, alert `requests.recovery`; recovery work keeps its unit (A34) | 0121/5e | yes | build | done (#809) |
| G06 | The participant's immediate pages and the claimed request's page in `e2e/pages.json` (axe, Lighthouse, keyboard). Not reachable while the gate is shut: the pages render an immediate form only for an active pool flow, which the flow resolver refuses while `ROLLBACK_SAFE` is `False`, and the e2e stack has no switch that opens it (a protection is never a setting). So the entries land in the PR that opens the gate, seeded from G08's fixture, and that PR runs with `ci:full` | 0121/32 | yes | build | follow-up |
| G07 | The upgrade matrix with released images: the v0.6.0 fixture and image exist (first release with a fixture), and `scripts/upgrade-matrix.sh` gains a mode that writes the gated records with the new image (a pool slot, a request with its record, an attempt, an immediate request), then starts the previous image on that database without a downgrade and books and claims there (ADR 0121 point 9, A30) | 0121/33 | yes | release, build | open |
| G08 | The full demonstration fixture: a video flow and an in-person flow on one shared pool, with a third independent flow; G06, G07 and G09 run on it | 0121/10 | yes | build | follow-up |
| G09 | Every row of the acceptance matrix A01 to A36 (`docs/service-flows-handover.md`) names its test, green with the gate opened in the test | 0121/34 | yes | build | open |
| G10 | The product owner's decision to open the gate, after G01 to G09 | step 8 | yes | po | open |
| G11 | A downgrade refused part way stopped between revisions (Alembic committed every SQLite migration on its own); the migration run is now one transaction (`transactional_ddl=True` in `migrations/env.py`) | step 8 | no | build | done (#786) |
| G12 | Diff the generated OpenAPI document against a stored copy (CLAUDE.md asks for it); today one test holds that `/api/v1` requires no `flow` | 0121/35 | no | build | open |
| G13 | The watchdog ends the task at the platform when it expires or cancels a contact-center attempt | 0121/27 | cc | build | follow-up |
| G14 | The own queue shows work the contact center holds as held, not as open | 0121/28 | cc | build | follow-up |
| G15 | See on the test host that Webex Contact Center's task events carry the origin, so a late task is matched | 0121/29 | cc | operator | follow-up |
| G16 | Immediate work dispatched to the contact center: a reference for work without a booking and the matching of its late events | 0121/30 | no | build | follow-up |
| G17 | The staff detail's table "what this person may pick up, and why" | 0121/4c | no | build | planned |
| G18 | The participant's choice between several flows of a service (one default per path and an explicit `flow` work without it) | 0121/5c | no | build | follow-up |
| G19 | Manual topics say *Dienst / Service* (next major release) | 0121/9 | no | po | follow-up |
| G20 | Internal names follow the vocabulary (`Group` to `ServiceOffering`), by expand/contract | 0121/11 | no | build | follow-up |
| G21 | Skills and pools delegated below the organisation admin | 0121/12 | no | po | follow-up |
| G22 | Named staff allocations with interval exclusivity | 0121/13 | no | po | follow-up |
| G23 | Overflow between capacity pools | 0121/14 | no | po | follow-up |
| G24 | Skills with a level, alternatives or relaxation over time | 0121/15 | no | po | follow-up |
| G25 | Skill synchronisation with the contact center, both directions | 0121/16 | no | po | follow-up |
| G26 | A subject catalogue shared by the services of an organisation | 0121/17 | no | po | follow-up |
| G27 | Joint planning of a person and a workstation or room | 0121/18 | no | po | follow-up |
| G28 | Workforce-calendar integration | 0121/19 | no | po | follow-up |
| G29 | Ticket printing and management of intake devices | 0121/20 | no | po | follow-up |
| G30 | A further contact-center platform adapter on the channel-independent task contract | 0121/21 | no | po | follow-up |
| G31 | The role-name rule of ADR 0077 also refuses the previous shipped names | 0121/22 | no | build | follow-up |
| G32 | The built-in service admin may manage the flows of its own service (a decision PR on the authorisation matrix) | 0121/23 | no | po | follow-up |
| G33 | Split the accessibility suite over several runners | 0121/24 | no | build | follow-up |
| G34 | A telephone interaction through the contact center (its own privacy decision) | 0121/25 | no | po | follow-up |
| G35 | A chat interaction kind (an ADR) | 0121/26 | no | po | follow-up |
| G36 | The participant checks in, or is told it is their turn, by themselves | 0121/31 | no | po | follow-up |
| G37 | One staffing route factory shared by the service and the pool tabs | 0122/4 | no | build | follow-up |
| G38 | `_week.html` takes its base path from one variable for both tabs | 0122/4 | no | build | follow-up |
| G39 | A zone change shown on the week card, and a notice for the daylight saving gap | 0122/4 | no | build | follow-up |
| G40 | The older queue routes answer refusals through the shared `or_list` helper, as the routes of step 7 do (code quality of #781); the claimed booking's live fragment keeps its own handler, as it answers with a fragment, not the list | step 8 | no | build | done (#786) |
| G41 | Utilisation of a counted pool in the reports (C1 to C3 per pool), and the outcome of work completed after its slot was closed (work in recovery among it) | 0122/5 | no | build | follow-up |

## Not on this list, on purpose

- Everything built: the rows marked `merged` in both ADRs.
- The options the product owner rejected (every request is a booking): no row, so no item.
