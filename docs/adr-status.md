# ADR build status

What of each decision runs in the code on `main`, as opposed to being written down only.
Checked against the code on 2026-10-30 (`main` at the merge of #155). Evidence is PR numbers
and files. Nothing here says it runs on a given installation: the test host's version is on
`/admin/updates`.

POLICY is a rule or process with no code to build; the column *What is missing* then says how
it is enforced and what is not.

Why this file exists: the product owner found that many ideas were recorded as an ADR and
never built, and had to look for the results themselves. From ADR 0059 point 7 on, every ADR
carries its own *Build status* table, updated by the PR that builds a step, and its Status
line says what is built. This table is the overview and is refreshed at the end of a work
package; when the two disagree, the code wins.

| Built? | ADRs |
|---|---|
| BUILT | 47 |
| PARTIAL | 21 |
| NOT BUILT | 6 |
| POLICY | 3 |

| ADR | Title | Built? | Evidence | What is missing |
|---|---|---|---|---|
| 0001 | Record architecture decisions | POLICY | `docs/adr/` (template #52); the PR template asks for ADR status (ADR 0059); `tests/test_adr_status.py` checks the list | No CI check on numbering or on "ADR in the same PR"; only review and the PR template |
| 0002 | Runtime layout of the skeleton | PARTIAL | #1, #4; migrations only in `web`; `api/health.py`; `jobs/healthcheck.py`; SQLite pragmas in `adapters/db/engine.py` | Per-request logging with route templates (§5): the access log stays off; Referrer-Policy is `same-origin`, not `no-referrer` |
| 0003 | Licence exception for certifi | BUILT | #1; `scripts/check-licenses.sh` (`IGNORED="certifi"`), CI `pip-licenses`, `docs/dependencies.md` | – |
| 0004 | Internationalisation module | PARTIAL | #1, #19, #85, #168; `app/i18n/` (negotiation, `.po`, `missing_key`), `tests/i18n/`, import-linter contract, `group_texts`; organisation texts: ADR 0074, migration 0074 | The global (superuser) override layer and its editor, `.po` export and import, a revision counter (the 5 s re-read of ADR 0074 stands in), locales added in the portal |
| 0005 | Modularity and documentation | POLICY | #1; ruff `D` (google), import-linter layers, jscpd at 1%, each a CI job | Reviewer check of module overlap is not machine-checked; adapters use hand-written expectations, no recorded fixtures |
| 0006 | Test tooling, image publishing and signing | BUILT | #1, #4, #94, #131; `e2e/`, `make mutation`, `.github/actions/publish-image` (cosign, SBOM) | – (CI results on GitHub not checked here) |
| 0007 | Dispatch through the WxCC Tasks API | BUILT | #10; `ContactCenterDispatcher` port, `adapters/wxcc.py`, `verify_wxcc` (HMAC, 300 s); points 1–2 superseded by ADR 0018 | Live behaviour against a WxCC tenant is not visible in the code |
| 0008 | Integration secrets in the admin UI, encrypted | PARTIAL | #10, #19, #106; `adapters/crypto.py` (AES-256-GCM), write-only inputs, audit, `integrations.manage` locked to the superuser | Key id per value and re-encryption on rotation: a new `SETTINGS_ENCRYPTION_KEY` makes secrets unreadable (runbook) |
| 0009 | Notification channels, reminder rules, templates | BUILT | #2, #19, #168; `domain/channels.py`, `job_plan.py`, `message_texts.py`, `admin/message_routes.py`; organisation layer: ADR 0074 | – (WhatsApp stays a later channel kind, as the ADR says) |
| 0010 | Live meeting status for group admins | BUILT | #10, #81, #167 (AG-3); `adapters/webhooks.py`, `domain/calls.py`, `admin/_agenda_row.html`, `MeetingStatusService` | Webex webhooks carry no timestamp, replays are caught by `event_key` |
| 0011 | Derived management tokens | BUILT | #10; `domain/tokens.py`, migration 0003, `tests/domain/test_tokens.py` | – |
| 0012 | Merge only on the product owner's instruction | POLICY | CLAUDE.md, Git section; merge commits record the instruction; merge queue in `ci.yml` | No check that the instruction is recorded; the ADR says five review questions, the PR template has seven |
| 0013 | Admin login with OIDC | BUILT | #7; `adapters/oidc.py` (PKCE, nonce, PyJWT), `admin/session.py`, Keycloak in `docker-compose.yml` | – (the session holds roles and scope as well, more than the ADR says) |
| 0014 | Authorisation matrix at the core | BUILT | #7, #8; `domain/authorization.py`, `admin/authz.py`, matrix and docs tests | – |
| 0015 | Admin changes need a recent login | BUILT | #8; `RequirePermission(fresh=)`, `admin_reauth_minutes`, `forms.safe_next` | – (queue next, claim, release and the update snooze are not fresh, by design) |
| 0016 | Admin forms without JavaScript | BUILT | #7, #8; `HTML_CSP`, `admin.css`; amended by ADR 0048, 0061, 0072 | – (HTMX is not used; own scripts on three pages) |
| 0017 | Video calls through the Webex Meetings API | BUILT | #10, #12, #138, #154; `adapters/webex_meetings.py`, `webex_auth.py`, `api/join_pages.py` | `templateId` per group ("later" in the ADR); blur and backgrounds are a runbook checklist |
| 0018 | Dispatch to WxCC as work items | BUILT | #10, #12, #125; `adapters/wxcc.py` (`task_body`), `verify_wxcc`, migration 0007; point 5 superseded by ADR 0021 | – |
| 0019 | Direct video call per group | BUILT | #10, #24, #155, #164 (AG-2, leave from the waiting page); `api/direct_pages.py`, `domain/direct_leave.py`, `direct_queue.py`, migration 0064 (switch moved by ADR 0072) | – |
| 0020 | Citizen calendar, branding through tokens | BUILT | #11, #49; `calendar.js`, `slot_picker.py`, `SCRIPT_CSP`, `/o/{slug}/theme.css`, `domain/branding.py` | – |
| 0021 | Ending the work item of a cancelled booking | BUILT | #12; `CallService.end_task`, `WxccDispatcher`, job `END_WXCC_TASK`, `diagnostics.py` | – |
| 0022 | Channels per organisation, SMS, editable texts | BUILT | #19, #29, #168; migration 0013; `admin/channel_routes.py`, `message_routes.py`, `adapters/senders.py`; the organisation-wide editor: ADR 0074 | – |
| 0023 | Machine API for contact-center bots | PARTIAL | #18, #67, #97; migration 0014; `api/agent/`, `domain/api_clients.py`, `docs/agent-api.md` | `POST` and `GET /change-requests` and scope `bookings:request_change` ("next step" in the ADR) |
| 0024 | Emergency closure | BUILT | #21, #73; migration 0015; CLI `closure` | – |
| 0025 | Holiday calendars | BUILT | #20; migration 0016 | Holiday names on the citizen calendar ("later" in the ADR) |
| 0026 | Direct-call cap, queue feedback | BUILT | #24; migration 0017 | WxCC live statistics (deferred by the ADR) |
| 0027 | Staffing blocks | PARTIAL | phase A #27, phase B #32 | Phase C rename: `ScheduleRule`, `TimeWindow`, `schedule_rules` keep the old names |
| 0028 | Provider-neutral SMS gateway | BUILT | #29; migration 0019; `adapters/sms.py` | – |
| 0029 | Updates from the portal | BUILT | #33, #36; migration 0020; `scripts/updater/` | Quiet-hours scheduling ("not yet" in the ADR) |
| 0030 | HTTPS via ACME (Caddy) | BUILT | #35; `deploy/proxy/`; reworked by ADR 0064 | – |
| 0031 | Audit log per level | BUILT | #34; migration 0021 | – |
| 0032 | Provider-neutral contact center | NOT BUILT | no `ContactCenterKind`, `group_contact_center` or `ContactCenterTask`; `wxcc_*` remain | Everything (CC-0 to CC-6) |
| 0033 | Context-sensitive help | PARTIAL | #43; 20 topics nl/en; `(i)` markers on 8 topics | 28 admin routes still in `NOT_YET_DOCUMENTED` |
| 0034 | Audit events to a log platform | BUILT | #72; `adapters/event_sink.py` | – (the client's security review AU-3 is not code) |
| 0035 | Embedding | BUILT | #83, #85, #86, #87; migration 0037 | – |
| 0036 | Scaling envelope | NOT BUILT (policy part: document only) | `adapters/db/backup.py` is the SQLite backup of ADR 0029 | SC-1 rate-limit storage, SC-3 backup interface, SC-4 metrics, SC-5 runbook triggers; SC-2 unsure |
| 0037 | Closure page, scenarios, image | BUILT | #50, #55, #60, #74, #75, #76 | – |
| 0038 | Maintenance mode | BUILT | #65 to #69; migration 0029 | – |
| 0039 | Adjustable roles, auditor | BUILT | #45, #48, #71; migration 0030 | – |
| 0040 | Event schema, storage limits | BUILT | #47, #70, #79, #84; migrations 0034, 0035 | – |
| 0041 | Extended branding | BUILT | #49; migration 0023 | – |
| 0042 | Settings layers, env lock | PARTIAL | steps 1–5, 6a, 6b-1, 7, 8 (#77 … #126) | Step 6b-2: generated, rotatable webhook secrets; the move to the organisation connection waits on ADR 0032 |
| 0043 | Own queue per group | PARTIAL | #88, #89, #90; refresh with ADR 0072 (#155) | System event "not picked up", lobby-join time, dashboard flag |
| 0044 | Planner agenda | BUILT | #81, #92, #98; migrations 0045, 0046 | – |
| 0045 | Dedicated Webex site, meeting policy | NOT BUILT | none | Everything |
| 0046 | Staff directory, SCIM | PARTIAL | step 1 #61; migration 0026 | Step 2 (suspend, end sessions, erase, retention, CSV), step 3 (SCIM) |
| 0047 | Admin information architecture | PARTIAL | steps 1–2 (#62, #80): `web/navigation.py`, `docs/admin-map.md` | Steps 3–6: organisation page with tabs, six group tabs, readiness checklist, `return` keys |
| 0048 | Live update progress | BUILT | #63; migration 0027 | – |
| 0049 | Role and scope in one claim | NOT BUILT | ADR only (#64) | Everything |
| 0050 | Host installer | BUILT | #82; `scripts/install.sh`; extended by #139, #144 | – |
| 0051 | Dependency currency | BUILT (repository side) | #94; `renovate.json`, `make pins`, `freshness.yml` | Unsure: the Renovate app installed, the required check on `main` (GitHub settings) |
| 0052 | Releases and versioning | BUILT | #96; `release_plan.py`; v0.3.0 (#113) | – |
| 0053 | Security events, public entry points | BUILT | #97; `web/security_events.py` | – |
| 0054 | EUPL-1.2 and CLA | BUILT (code side) | #102, #104; `reuse lint` | Not code: the lawyer's review of the CLA, making the repository public |
| 0055 | Booking form fields | BUILT | #114; migration 0053 | – |
| 0056 | Citizen confirms by email | PARTIAL | step 1 #115, step 2 #134 (migration 0058) | Step 3 (planner confirms on behalf), step 4 (citizen corrects details) |
| 0057 | Rebooking assistant | PARTIAL | step 1 #129 (`domain/rebooking.py`) | Steps 2–4: held proposal, message, confirm and expiry, review page, automatic mode, `reschedule_*` settings |
| 0058 | Fast PR lane, full queue lane | PARTIAL | #122, #124 | Decision 6 (cheaper matrix sweep); unsure: GitHub required check and the `ci:full` label |
| 0059 | Release candidates, update channels | BUILT | #131, #132, #140 | Points 4 and 6 amended by ADR 0067 |
| 0060 | Product logo | PARTIAL | #135 (LG-1 to LG-3) | LG-4 optional motion |
| 0061 | Live overview with widgets | PARTIAL | #141, #150, #153 | `overview-live.js`, alerts and pop-ups, `QUEUE_WAIT_*`, SH-3 |
| 0062 | Personal overview layout | NOT BUILT | none | Everything (needs ADR 0046 step 2) |
| 0063 | Personal data in the portal | BUILT | #143 | – |
| 0064 | HTTPS by default, certificate status | BUILT | #139, #144; step 3 is ADR 0070 | – |
| 0065 | Webex reason, probe meeting | BUILT | #138, #142; migration 0056 | – |
| 0066 | Bookings per email address | PARTIAL | #128; migration 0054 | Point 8: a refusal is explained by email only (ADR 0056 step 2 is merged) |
| 0067 | Version management in the portal | PARTIAL | steps 1–2 #145, #146; step 3 #148 in the merge queue; step 5 (check interval, "Check now") #170 in PR; migrations 0070, 0071, 0073 | Step 4: the live check on docker01 |
| 0068 | Troubleshooting per booking | BUILT | #152; migration 0059 | – |
| 0069 | Citizen cancels until the start | BUILT | #151; migration 0062 | – |
| 0070 | Certificate source in the portal | BUILT | #149; migration 0061 | – |
| 0071 | Trial call on Webex | BUILT | #154; migration 0063 | – |
| 0072 | Direct calls per block, live status | PARTIAL | #155 (step 1); migration 0064 | Step 2: the updates page on `live-region.js` (a follow-up listed in the ADR) |
| 0073 | Email deliverability: sender domain check, system mail headers | PARTIAL | step 1 #166; no migration | Step 2 (a periodic check in the worker) is decided after step 1 is in use (ADR decision 6) |
| 0074 | Organisation text overrides | BUILT | #168; migration 0074; `domain/text_overrides.py`, `adapters/db/text_overrides.py`, `admin/organisation_text_routes.py` | Step 2: the global (superuser) layer, `.po` export and import, revision counter (follow-up, listed in the ADR) |
| 0075 | Browser-direct join link per group | PARTIAL | Steps 1 and 2 (PR #171); migration 0075 | Step 3: confirmation with the Webex app installed, on docker01 |
| 0076 | Participant journey: trial call events, the page sent to | PARTIAL | Step 1 (PR #171); no migration | Step 2: the signal *followed, nobody entered*; step 3: the merged journey view |
| 0077 | Terminology: participant, and organisation names for staff functions | NOT BUILT | accepted 2026-10-31; step 1a (column rename) in PR #172; `CLAUDE.md` wording changed with the ADR | Everything but step 1a: the rename (steps 2a to 2d), the guard test, function names per organisation and group, the column rename |

## Corrected on 2026-10-30 (second pass: ADR 0001 to 0023)

- ADR 0001 to 0023 were not in this list. Each was checked against the code by reading the ADR
  and finding what runs, not by trusting its own words. Seven turned out partly built (0002,
  0004, 0008, 0009, 0010, 0019, 0023; 0019 was completed by AG-2 afterwards) and now say so in their Status line; three are policy
  (0001, 0005, 0012). Two things worth knowing: CLAUDE.md says organisation text overrides are
  editable in the admin UI, but the override store of ADR 0004 and ADR 0009 does not exist
  (group texts do); ADR 0010's polling fallback and 30-second refresh were never built.
- What it found that is not built is listed with a size and an owner of the decision in
  `docs/backlog-adr-gaps.md` (AG-1 to AG-7).
- A test (`tests/test_adr_status.py`) now fails when an ADR is missing from this list, when the
  counts are wrong, or when an ADR's Status line says something else than its row.

## Corrected on 2026-10-30

- Status lines said *proposed* for decisions that are built (0048, 0050, 0051, 0053, 0054,
  0063, 0064, 0070) or partly built (0047, 0057, 0060, 0061, 0072): now *accepted*, with the
  state. Four decisions with nothing built stay *proposed* (0032, 0036, 0045, 0062); 0049 is
  accepted and not built. Nine accepted ADRs now say they are partly built (0027, 0033, 0042,
  0043, 0046, 0056, 0058, 0066, 0067).
- Build status tables that still said "in PR" for merged work were brought up to date (0060 to
  0063, 0065, 0068, 0072); seven ADRs without a table got one (0056, 0057, 0064, 0066, 0069,
  0070, 0071).
- `docs/architecture.md` lists every decision and no longer marks the state of one; its
  roadmap rows for the widget, audit shipping, maintenance mode, roles and logging are done.
  `docs/HANDOVER.md` no longer says the build has not started. `docs/backlog-admin-portal-refresh.md`
  has a Status column like the other backlogs.

## Not decided by code

- The Renovate app and the branch protection of `main` (ADR 0051), the required check
  `authorisation matrix` and the label `ci:full` (ADR 0058): GitHub settings.
- The lawyer's review of the CLA and making the repository public (ADR 0054), the client's
  security review of audit shipping (ADR 0034, AU-3): not code.
