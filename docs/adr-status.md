# ADR build status (inventory of 2026-10-30, draft)

What of each decision runs in the code on `origin/main` (0811ecf), as opposed to being written
down only. Evidence is commits, PR numbers and files; nothing was run. This is a one-off
inventory; the intended home is a *Build status* section in every ADR, with this table
generated from it (see ADR 0059).

| ADR | Title (short) | Built? | Evidence | What is missing |
|---|---|---|---|---|
| 0024 | Emergency closure | BUILT | #21, #73; migration 0015; `domain/closure*.py`; CLI `closure` | Nothing in code |
| 0025 | Holiday calendars | BUILT | #20; migration 0016; `domain/holidays.py` | Holiday names on the citizen page ("later" in the ADR) |
| 0026 | Direct-call cap, queue feedback | BUILT | #24; migration 0017; `domain/direct_queue.py` | WxCC live statistics (deferred by the ADR) |
| 0027 | Staffing blocks | PARTIAL | Phase A #27, phase B #32 | Phase C: rename `ScheduleRule`/`TimeWindow`/`schedule_rules` to the staffing names |
| 0028 | Provider-neutral SMS gateway | BUILT | #29; migration 0019; `adapters/sms.py` | Nothing |
| 0029 | Updates from the portal | BUILT | #33, #36; migration 0020; `scripts/updater/` | Quiet-hours scheduling ("not yet" in the ADR) |
| 0030 | HTTPS via ACME | BUILT | #35; `deploy/proxy/`; profile `tls` | Nothing |
| 0031 | Audit log per level | BUILT | #34; migration 0021 | Nothing |
| 0032 | Provider-neutral contact center | NOT BUILT | none; WxCC names still everywhere in `app/` | Everything (CC-0 to CC-6) |
| 0033 | Context-sensitive help | PARTIAL | #43; 17 topics nl/en | 29 admin routes still in `NOT_YET_DOCUMENTED`; field markers on 6 topics only |
| 0034 | Audit events to a log platform | BUILT | #72, #97; `adapters/event_sink.py` | Nothing in code |
| 0035 | Embedding | BUILT | #83, #85, #86, #87; migration 0037 | Nothing |
| 0036 | Scaling envelope | NOT BUILT (policy part is document only) | none | SC-1 rate-limit storage setting, SC-3 backup interface, SC-4 metrics, SC-5 runbook; SC-2 unsure |
| 0037 | Closure page, scenarios, image | BUILT | #50, #55, #60, #74, #75, #76 | Nothing |
| 0038 | Maintenance mode | BUILT | #65 to #69; migration 0029 | Nothing |
| 0039 | Adjustable roles, auditor | BUILT | #45, #48, #71; migration 0030 | Property-based test unsure (no `hypothesis`) |
| 0040 | Event schema, storage limits | BUILT | #47, #70, #79, #84 | Nothing |
| 0041 | Extended branding | BUILT | #49; migration 0023 | Nothing |
| 0042 | Settings layers, env lock | PARTIAL | steps 1–5, 6a, 6b-1, 7, 8 (#77 … #126) | Step 6b-2: generated, rotatable webhook secrets |
| 0043 | Own queue per group | PARTIAL | #88, #89, #90 | Auto-refresh, lobby-join time, "not picked up" event, dashboard flag |
| 0044 | Planner agenda | BUILT | #81, #92, #98 | Nothing found |
| 0045 | Dedicated Webex site, meeting policy | NOT BUILT | none | Everything |
| 0046 | Staff directory, SCIM | PARTIAL | step 1 #61 | Step 2 (suspend, erase, retention, CSV), step 3 (SCIM) |
| 0047 | Admin information architecture | PARTIAL | steps 1–2 (#62, #80): navigation register, admin-map | Steps 3–6: tabbed organisation page, sub-tabs, readiness checklist, `return` |
| 0048 | Live update progress | BUILT | #63; migration 0027 | Nothing |
| 0049 | Role and scope in one claim | NOT BUILT | ADR only (#64) | Everything, including the legacy safety net |
| 0050 | Host installer | BUILT | #82; `scripts/install.sh` | Nothing found |
| 0051 | Dependency currency | BUILT (repo side) | #94; `renovate.json`, `make pins`, `freshness.yml` | Unsure: Renovate app installed, required check re-added |
| 0052 | Releases and versioning | BUILT | #96; v0.3.0 released | Nothing |
| 0053 | Security events, public entry points | BUILT | #97; `web/security_events.py` | Nothing found |
| 0054 | EUPL-1.2 and CLA | BUILT (code side) | #102, #104; `reuse lint` | Lawyer review of the CLA, repository public: not code |
| 0055 | Booking form fields | NOT ON MAIN | open PR #114 | Whole ADR |
| 0056 | Confirm booking by email | PARTIAL | step 1 #115 (domain only) | Steps 2–4: migration, adapter, worker, mails, confirm page; bookings still created as `confirmed` |
| 0057 | Rebooking assistant | NOT ON MAIN | open PR #120 (ADR only) | Whole ADR |
| 0058 | Fast PR lane, merge-queue lane | PARTIAL | #122 | Step 6 cheaper matrix sweep; GitHub settings (required check, `ci:full` label) unsure |

## Written status that no longer matches the code

- Status "proposed" while built: 0048, 0050, 0051, 0053, 0054; 0047 is "proposed" and binding in CLAUDE.md.
- 0040 and 0034 say "env until the ADR 0042 registry exists"; the registry exists.
- 0043 *Consequences* describes a `BookingDispatcher` port that was never built; *Acceptance* has the as-built version.
- 0051 calls the installer an open PR (merged as #82); 0052 says pyproject is at 0.2.0 (now 0.3.0).
- `docs/architecture.md` roadmap: WP3c "planned" (built), 0034/0035/0043/0048/0050 "(proposed)" (built), 0038 "planned" (built), 0039 "in progress" (built), 0040 "step 1 done" (all four parts built); 0042, 0046, 0047, 0049, 0051–0054, 0056, 0058 missing.
- `docs/backlog-architecture-gaps.md`: EM-1 to EM-5 not marked done (built). `docs/backlog-closure-and-maintenance.md`: CB-1, CL-1, CL-2 not marked done (built).
- `docs/HANDOVER.md` header still says "bouw nog niet gestart".
