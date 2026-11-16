# Before the rollback gate opens: the checklist of ADR 0121 and ADR 0122

`ROLLBACK_SAFE` in `app/domain/service_flows.py` is `True` since #826: the product owner opened
the gate on 2026-11-15 (G10) for flows on the own queue, after every item that blocks it was
done. Flows that dispatch to the contact center stay refused by `CONTACT_CENTER_FLOWS_READY`
until G13 to G15 are done (below). While the gate was shut no non-legacy flow could be
activated, named or reached, and a rollback to the previous image was safe (ADR 0122 decision
12). This list was everything that had to be done before a pull request could set it to `True`,
built from the open rows of the two ADRs' *Build status* tables and the findings of the
compatibility proof ([`service-flows-compatibility.md`](service-flows-compatibility.md), ADR 0121
step 8); it stays as the record, and as the list of what still holds contact-center flows.

Opening the gate is a decision PR (it changes the behaviour of an accepted ADR and the sentence
in CLAUDE.md), merged only with `po:approved`, and only in an image whose previous supported minor
already holds the readers and the accounting of every record a non-legacy flow writes (ADR 0122,
consequences): at the earliest the first minor after 0.6.0.

Items G13 to G15 (*Blocks* `cc`) are enforced by code, not only listed (product owner,
2026-11-14): while the fixed constant `CONTACT_CENTER_FLOWS_READY` in `app/domain/service_flows.py`
is `False`, the readiness checklist refuses to activate a non-legacy flow that dispatches to the
contact center, with the reason `contact_center_not_ready`, in the portal and in the
configuration import alike (one rule, in the rollback point of `app/domain/flow_readiness.py`).
Flows on the own queue and the legacy flows of existing services are not affected. The constant
is a protection, never a setting; the PR that closes G13 to G15 flips it to `True`. G13 and G14
are build work; G15 is an operator check that a person does on the test host with a real Webex
Contact Center tenant (`docs/runbook.md`, *Checklist G15*), and it is the last thing before the
constant flips.

The same point holds a second rule, which is not an item because it never lifts (product owner,
2026-11-15; ADR 0121 row 40, #895): once the gate is open, a non-legacy flow that dispatches to
the own queue is not activated on a service without an own queue (`Group.has_own_queue`, the
service's `dispatch_kind` is `openplanner_queue`), with the reason `own_queue_missing`, in the
portal and in the configuration import alike. Without that queue nobody can claim the flow's
work and the watchdog expires it. While the gate is shut the checklist still names only
`rollback_not_safe`.

`tests/test_service_flows_compatibility.py` reads this table: every open row of either ADR has an
item here, and `ROLLBACK_SAFE` may be `True` only while no item with *Blocks* `yes` is open.

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
| G06 | The participant's immediate pages and the claimed request's page in `e2e/pages.json` (axe, Lighthouse, keyboard). Not reachable while the gate is shut: the pages render an immediate form only for an active pool flow, which the flow resolver refuses while `ROLLBACK_SAFE` is `False`, and the e2e stack has no switch that opens it (a protection is never a setting). Built (#825): `python -m app.cli seed-demonstration` (`app/adapters/db/demonstration_stack.py`) runs `seed_demonstration` on `demo` after `seed-demo`, gives the document review the on-demand path, staffs its pool around the clock with two units and leaves `demo` without subjects; `seed-closures`, which the e2e stack runs, carries it; the Playwright set-up `e2e/requests.ts` sends the immediate form twice and has the e2e agent pick up the oldest request, and `e2e/pages.ts` reads its values when a page opens (`{immediate_request}`, `{claimed_request}`, the request cookie). Done with the gate (#826): the entries in `e2e/pages.json` for `/g/demo/direct?flow=documentcontrole` and `/r/{ref}` (`island`) in nl and en and for `/admin/groups/demo/queue/requests/{id}`; they landed in the PR that opened the gate; evidence: CI run 43557257527 on head `44fedcbd` of #826 with the gate open: axe in chromium, firefox and webkit, Lighthouse 100 for the immediate form and `/r/{ref}` (nl, en; the request page answers 200 behind its cookie, #841), the five island checks of `/r/{ref}` (scripts off, keyboard, 320 px, forced colours and reduced motion, no CSP refusal), the claimed request at 375, 768 and 1280 px, and the overview's queue widget at 1280 px | 0121/32 | yes | build | done (#825, #826) |
| G07 | The upgrade matrix with released images: the v0.6.0 fixture and image exist (first release with a fixture), and `scripts/upgrade-matrix.sh` gains a mode that writes the gated records with the new image (a pool slot, a request with its record, an attempt, an immediate request), then starts the previous image on that database without a downgrade and books and claims there (ADR 0121 point 9, A30). Built (#825): `scripts/upgrade-matrix.sh rollback IMAGE [FIXTURE]` migrates FIXTURE (or a fresh database) with this checkout, runs `seed-demo` and `seed-gated-records` (`app/adapters/db/gated_records.py`: the demonstration, a booking on the shared pool with its request in treatment, a second one with a contact-center attempt, an immediate request with its record), starts IMAGE on it without a downgrade (`UPGRADE_PLATFORM` for another architecture; a clear error when IMAGE cannot be had), books on `/g/demo` on the day the pool bookings hold and compares the database per table; `rollback-release IMAGE_REPO` downloads and checks the newest release fixture and runs it, and skips with a notice while no release has one. Run locally: against an image of `main`, fresh and on a fixture that image wrote, and against the public v0.5.0 image; all booked and compared clean. The CI step is #829 (`ci.yml`, job `upgrade matrix (released fixtures)`, merged). Proven (2026-11-15, #889): v0.6.0 is released (tag on 8e5610bd, published 19:58 UTC) with `upgrade-fixture-0.6.0.sqlite` (sha256 `59668917422dbe4836433567e3963ba1c878351fed0bbe6b1df19b15ae7df7f2`, checked by the mode) and the public image `ghcr.io/3corners-labs/openplanner-public@sha256:0f17ee734437232c6a21a5460c461a3f918fc374c0c870ba44cf2edaae26b73a` (cosign verified). `scripts/upgrade-matrix.sh rollback-release ghcr.io/3corners-labs/openplanner-public` (`UPGRADE_PLATFORM=linux/amd64` on an arm64 host) migrated that fixture with this checkout, wrote the gated records, started the v0.6.0 image on it without a downgrade, booked on `/g/demo` (HTTP 303) and compared the database: the only finding was the live booking the image made. Every CI run before the release skipped the step with its notice; the first run after it is the merge-queue run of #889, which pulls `ghcr.io/3corners-labs/openplanner:0.6.0`. The claim in the previous image is G43 (#896) | 0121/33 | yes | release, build | done (#825, #829, #889) |
| G08 | The full demonstration fixture: a video flow and an in-person flow on one shared pool, with a third independent flow; G06, G07 and G09 run on it. Built: `app/adapters/db/demonstration.py` (#819), the video flow with G42 | 0121/10 | yes | build | done (#819) |
| G09 | Every row of the acceptance matrix A01 to A36 (`docs/service-flows-handover.md`) names its test, green with the gate opened in the test. Built: section 4 of the compatibility proof; A30 with released images is G07, A32 in the browser is G06 | 0121/34 | yes | build | done (#819) |
| G10 | The product owner's decision to open the gate, after G01 to G09. Decided 2026-11-15 in the conversation, after the own-queue rule (#895) and the claim in the previous image (G43, #896) were merged; built: `ROLLBACK_SAFE = True` with the e2e pages of G06 (#826) | step 8 | yes | po | done (#826) |
| G11 | A downgrade refused part way stopped between revisions (Alembic committed every SQLite migration on its own); the migration run is now one transaction (`transactional_ddl=True` in `migrations/env.py`) | step 8 | no | build | done (#786) |
| G12 | Diff the generated OpenAPI document against a stored copy (CLAUDE.md asks for it); today one test holds that `/api/v1` requires no `flow` | 0121/35 | no | build | open |
| G13 | The watchdog ends the task at the platform when it expires or cancels a contact-center attempt. Built: the round plans the existing end-task job (ADR 0021) for an attempt it ends that holds a task, never beside a cancellation's own; a late task of an attempt it expired in doubt is ended as an orphan; the job's outcome is on the booking's timeline | 0121/27 | cc | build | done (#893) |
| G14 | The own queue shows work the contact center holds as held, not as open. Built: the row says *held by the contact center*, offers no claim, is never next and never "not picked up" | 0121/28 | cc | build | done (#894) |
| G15 | See on the test host that Webex Contact Center's task events carry the origin, so a late task is matched. The last item before `CONTACT_CENTER_FLOWS_READY` flips: a person does it on the test host with a real tenant, by the procedure in `docs/runbook.md` (*Checklist G15: does a task event carry the origin?*), and records the result here | 0121/29 | cc | operator | follow-up (enforced by code until done) |
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
| G41 | Utilisation of a counted pool in the reports (C1 to C3 per pool), and the outcome of work completed after its slot was closed (work in recovery among it). Built (#897): per pool, on the organisation's *Usage*, *Capacity* view (no second home), C1 to C3 in unit-hours of the pool's own staffing (`app/domain/usage_report_pools.py`, rule in `docs/reporting.md`), for a reader of the whole organisation, small cells hidden, not exported; and `booking.outcome` written when work is completed after its slot was closed, in the hour of the slot (`slot_close.late_completion`), by the queue's completion, the contact-center `task:ended` and a supervisor completing work in recovery | 0122/5 | no | build | done (#897) |
| G42 | Video for a new flow (`BUILT_KINDS`), so the demonstration fixture gains its video flow; the gate may open for flows without video first. Built: a video flow is request work with one meeting per pool booking (the existing meeting jobs), one rule for which booking is a video call (`has_video`, `app/domain/booking_video.py`), readiness reason `video_host_missing`, the fixture's *Samen bespreken* on the shared pool. Still for a person: a real Webex meeting of such a booking on the test host (see the PR) | 0121/36 | no | build | done (#906) |
| G43 | The claim of a record in the previous image (A30). Built (#896): the previous image's claim is a signed-in agent's POST (`POST /admin/groups/{slug}/queue/{booking_id}/claim`, `queue.work`), which the matrix cannot make (no identity provider, and a login is never forged); what it writes is one conditional update of a confirmed, unclaimed booking (`claim_booking`). `scripts/rollback_claim.py` runs that update with whichever `app` package is on the path: `scripts/upgrade-matrix.sh rollback` pipes it into the running previous image, so the image's own code (`/srv/app`) claims on the expanded database a confirmed booking in its own shape (`legacy_booking` of `seed-gated-records`, pre-train columns only) once, is refused the second claim, and is refused the booking whose work this checkout holds; this checkout then reads the claim back and the held work keeps its holder. Run on 2026-11-15 on the public v0.6.0 image (`UPGRADE_PLATFORM=linux/amd64`): `[true, false, false]`, read back as `previous-image` and `gated-records`, database compared (the one finding is the live booking the image made). At the database without Docker: `tests/test_upgrade_train.py::test_the_previous_releases_own_claim_is_safe_on_the_expanded_schema` runs the v0.6.0 tag's own `app` package (skips without the tag; the matrix step is the one that always has the image). Found and fixed: the started record of `seed-gated-records` held no booking claim, which `QueueService` writes before it opens the work, so the previous image claimed work the current code held (`[true, false, true]`); it now carries it (`test_the_started_booking_carries_the_booking_claim_the_previous_image_checks`). Not proven: the route itself in the previous image (session, permission, skills, audit; row 44, G44) and a booking with an open contact-center attempt (G44) | 0121/38 | no | build | done (#896) |
| G44 | The previous image's claim through its own route and on a booking with an open contact-center attempt (A30, what G43 leaves). Two parts: the POST to the queue in the v0.6.0 image with a signed-in agent (the matrix needs an identity provider: the bundled one of ADR 0090, or the e2e stack), which proves the session, the permission, the skills check and the audit row next to the gated records; and the booking of a contact-center attempt, which carries no booking claim (a dispatch is not an own-queue claim), so on an own-queue or fallback group the previous image's claim takes it (`claim_booking` is true on it). Decide: the current code writes a booking claim with the attempt, or a rollback is refused while an attempt is open (0114 already refuses the downgrade, not a previous image started without one) | 0121/39 | no | build | follow-up |

## Not on this list, on purpose

- Everything built: the rows marked `merged` in both ADRs.
- The options the product owner rejected (every request is a booking): no row, so no item.
