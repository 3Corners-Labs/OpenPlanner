# Compatibility of the services-and-flows train (ADR 0121 step 8)

ADR 0121 point 9 and ADR 0122 decision 12 say a non-legacy flow may not be activated until the
whole train has been proven against what OpenPlanner did before it. This document is that proof:

1. the **compatibility matrix**: every behaviour that existed before the train, against every
   change of the train that touches it, each cell with the test that proves the behaviour
   unchanged or proves the documented, intended change;
2. the **upgrade and rollback proof** of ADR 0116 (expand/contract) for migrations 0109 to 0115;
3. what the proof does not cover, which moves to the checklist of the rollback gate,
   [`service-flows-gate-checklist.md`](service-flows-gate-checklist.md).

`tests/test_service_flows_compatibility.py` reads this file: every test it names must exist,
every old behaviour below must have a row whose proof is a test or an item of the checklist, and
the gate must stay shut while the checklist has an open item that blocks it. A test renamed or removed without this file fails there.

Words: *unchanged* means a pre-train service (a group with its legacy pool and legacy flows,
migration 0109) behaves byte for byte as before. *Intended* means a change the ADRs decided,
written next to the proof. *Gated* means a path that only a non-legacy flow reaches, and
`ROLLBACK_SAFE` (`app/domain/service_flows.py`) keeps every non-legacy flow shut.

## 1. Compatibility matrix

The columns are the train's changes: **B** the backfill and the flow resolver (steps 2b, 2e, 3a),
**S** skill enforcement and skill codes (4a, 4b), **P** pool accounting (ADR 0122), **R**
requests, work items and the request-level record (5a, 5b), **C** the contact-center contract
and dispatch (6a, 6b), **I** immediate intake and check-in (7a, 7b). A behaviour a change does not
reach has no row for it.

### Group booking (standalone page and widget)

| Change | Result | Proof |
|---|---|---|
| B | Unchanged: every service gets a legacy pool and the default flow of what it offers; nothing is shared or inferred | `tests/test_migration_service_flows.py::test_every_group_gets_a_legacy_pool_and_its_default_flows` |
| B | Unchanged: every pre-train booking and slot reads as legacy after the upgrade (flow NULL, no pool) | `tests/test_upgrade_train.py::test_every_pre_train_booking_and_slot_reads_as_legacy_after_the_upgrade` |
| B | Intended (additive): a new booking records the default flow and its revision | `tests/api/test_flow_entry_paths.py::test_a_page_booking_records_the_default_flow_and_its_revision` |
| B | Unchanged: a service without flow rows (a rollback window) still books, without a flow | `tests/domain/test_services_flow.py::test_a_service_without_flow_rows_still_books_without_a_flow` |
| B | Intended: an explicit unknown, foreign or archived flow is refused; the default path keeps booking | `tests/api/test_flow_entry_paths.py::test_an_archived_flow_is_refused_but_the_default_path_keeps_booking` |
| B | Unchanged: a page without a flow reads no flows | `tests/api/test_flow_entry_paths.py::test_page_views_without_a_flow_read_no_flows` |
| S | Unchanged: a legacy flow requires no skill, so an agent without skills claims as before | `tests/domain/test_queue_skills.py::test_work_without_skills_is_claimed_by_an_agent_who_holds_none` |
| P | Unchanged: availability and booking read only legacy slots | `tests/adapters/db/test_legacy_slot.py::test_availability_and_booking_reads_see_only_the_legacy_slot` |
| P | Unchanged: every slot query states legacy-only or why not | `tests/adapters/db/test_legacy_slot.py::test_every_query_of_slots_chooses_legacy_only_or_is_listed_with_a_reason` |
| P | Unchanged: every path of a service is the same with a pool flow beside it | `tests/domain/test_pool_booking_gate.py::test_every_path_of_the_service_is_the_same_with_a_pool_flow_beside_it` |
| P | Gated: a pool flow cannot be named and nothing is stored while the gate is shut | `tests/domain/test_pool_booking_gate.py::test_with_the_gate_shut_the_pool_flow_cannot_be_named_and_nothing_is_stored` |
| P | Gated: a pool flow made the default is ignored while the gate is shut | `tests/domain/test_pool_booking_gate.py::test_a_pool_flow_made_the_default_is_ignored_while_the_gate_is_shut` |
| R | Unchanged: a legacy booking gets no request and is never watched | `tests/domain/test_request_watch.py::test_a_legacy_booking_is_never_watched` |

### Direct call (start now, ADR 0072)

| Change | Result | Proof |
|---|---|---|
| B | Unchanged: a weekly block that offers the direct call gives the service its direct flow | `tests/adapters/db/test_default_flows_contract.py::test_a_rule_that_offers_the_direct_call_adds_the_direct_flow` |
| B | Intended (additive): a direct call records the default flow of the on-demand path | `tests/domain/test_services_flow.py::test_a_direct_call_records_the_default_flow_of_the_on_demand_path` |
| B | Intended: an appointment-only flow named on the direct page is refused | `tests/api/test_flow_entry_paths.py::test_an_appointment_only_flow_is_refused_on_the_direct_page` |
| P | Unchanged: direct calls do not take scheduled capacity | `tests/domain/test_direct_call.py::test_direct_calls_do_not_take_scheduled_capacity` |
| C | Unchanged: starting creates the meeting and the work item at once | `tests/api/test_direct_pages.py::test_starting_creates_meeting_and_work_item_at_once` |
| I | Unchanged: the direct page offers the call while open; the immediate form replaces it only for an on-demand flow on a pool, which the gate keeps shut | `tests/api/test_direct_pages.py::test_group_page_offers_a_direct_call_while_open` |

### Trial call (ADR 0072)

No change of the train reaches the trial call: it reads the booking and the service's video
settings only, and no train module imports it.

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: opening schedules a meeting the participant can enter alone | `tests/domain/test_trial_call_service.py::test_opening_schedules_a_meeting_the_participant_can_enter_alone` |
| none | Unchanged: the booking and join pages link to the trial call | `tests/api/test_trial_pages.py::test_the_booking_and_join_pages_link_to_the_trial_call` |

### Contact-center dispatch (ADR 0018)

| Change | Result | Proof |
|---|---|---|
| C | Unchanged: the task body of every recorded case is byte for byte the recorded one | `tests/adapters/test_wxcc_recorded_bodies.py::test_the_body_is_byte_for_byte_the_recorded_one` |
| S | Unchanged: a task without skills carries no skill key | `tests/adapters/test_wxcc_skill_codes.py::test_a_task_without_skills_has_no_skill_key` |
| C | Unchanged: no other booking field or label reaches the payload | `tests/adapters/test_wxcc_routing_payload.py::test_no_other_booking_field_or_label_reaches_the_payload` |
| C | Unchanged: the meeting first, then the work item, idempotent | `tests/jobs/test_call_jobs.py::test_meeting_then_work_item` |
| C | Gated: a flow on the own queue is never dispatched to the contact center | `tests/domain/test_request_dispatch.py::test_a_flow_on_the_own_queue_is_never_dispatched` |

### Emergency closure (ADR 0024)

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: closing suspends covered bookings and keeps their data | `tests/domain/test_closure_service.py::test_closing_suspends_covered_bookings_and_keeps_their_data` |
| P | Intended (ADR 0122 P4): a closure suspends a pool booking as it does a legacy one | `tests/domain/test_closure_service.py::test_a_closure_suspends_a_pool_booking_as_well_as_a_legacy_one` |
| P | Intended: the closure's reads see the pool booking too | `tests/adapters/db/test_legacy_slot.py::test_a_closure_reads_the_pool_booking_too` |
| R | Intended: a booking on hold keeps its request open, the deadline follows a new time | `tests/domain/test_request_watch.py::test_a_booking_on_hold_keeps_its_request_open_and_the_deadline_follows_a_new_time` |

### Rebooking and proposals (ADR 0044, ADR 0057)

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: a proposal frees the old place and holds the new one | `tests/domain/test_reschedule_proposals.py::test_a_proposal_frees_the_old_place_and_holds_the_new_one` |
| none | Unchanged: proposals never overfill a slot | `tests/domain/test_rebooking.py::test_proposals_never_overfill_a_slot_and_ignore_input_order` |
| B | Intended: moving a booking keeps its flow | `tests/api/test_flow_entry_paths.py::test_moving_a_page_booking_keeps_the_flow` |
| P | Unchanged: a booking without a flow moves on the service's own times when the default is a pool | `tests/adapters/db/test_pool_booking.py::test_a_booking_without_a_flow_moves_on_own_times_when_the_default_is_a_pool` |

### Deletion of personal data (purge, ADR 0102)

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: every transition leaves one purge at the retention moment | `tests/domain/test_purge_invariant.py::test_every_transition_leaves_one_purge_at_the_retention_moment` |
| R | Unchanged: the purge clears exactly the fields of the privacy list | `tests/adapters/db/test_purge_remains.py::test_purge_personal_data_names_the_fields_of_privacy_and_no_others` |
| R | Intended (decision D1): the request-level record uses the booking's names, one list masks both | `tests/domain/test_privacy.py::test_the_request_record_uses_the_booking_names_so_one_list_masks_both` |
| R | Intended: the watchdog deletes a request-level record at its moment and not before | `tests/domain/test_request_watch.py::test_the_round_deletes_a_record_at_its_moment_and_not_before` |
| R | Intended: the nightly sweep deletes an overdue record the round missed | `tests/domain/test_request_watch.py::test_the_sweep_deletes_an_overdue_record_the_round_missed_and_counts_it` |

### Configuration export and import (ADR 0089, ADR 0099)

| Change | Result | Proof |
|---|---|---|
| B | Unchanged: a document without the new sections leaves skills, pools and flows alone | `tests/domain/test_configuration_v1_flows.py::test_a_document_without_the_sections_leaves_skills_pools_and_flows_alone` |
| B | Unchanged: the same, through the database adapter | `tests/adapters/db/test_config_flows.py::test_a_document_without_the_sections_leaves_what_the_installation_has` |
| B | Intended: export, import, export gives the same document | `tests/adapters/db/test_config_flows.py::test_export_import_export_gives_the_same_document` |
| R | Unchanged: every table is configuration or state, so requests never travel in a package | `tests/adapters/db/test_configuration_package.py::test_every_table_of_the_model_is_configuration_or_state` |

### Public API `/api/v1` (widget)

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: a booking returns no token and no personal data | `tests/api/test_widget_api.py::test_a_booking_from_an_allowed_origin_returns_no_token_and_no_personal_data` |
| B | Intended (additive, optional): the flow is in the widget schema | `tests/api/test_flow_entry_paths.py::test_the_flow_is_in_the_widget_openapi_schema` |
| B | Unchanged contract: no `flow` property is required anywhere in `/api/v1`, so an old client keeps working | `tests/api/test_flow_entry_paths.py::test_no_api_v1_schema_requires_a_flow` |
| B | Intended: a widget booking records the default flow | `tests/api/test_flow_entry_paths.py::test_a_widget_booking_records_the_default_flow` |

### Agent API (ADR 0023)

| Change | Result | Proof |
|---|---|---|
| none | Unchanged: the agent API books through the same booking service | `tests/api/test_agent_api.py::test_booking_service_is_shared` |
| none | Unchanged: a change request answers the same with or without a match | `tests/api/test_agent_change_requests.py::test_a_change_request_answers_the_same_with_or_without_a_match` |
| P | Open (gated): a requested time is judged on the service's own times, so a time on the pool of a pool booking is refused; must be fixed before the gate opens (ADR 0122 build status, step 5) | checklist G04 |

## 2. Upgrade and rollback proof (ADR 0116)

The train's migrations are 0109 to 0115, all in minor 0.6; the last revision before the train is
0108. Each one is expand-only and has its own test (`tests/test_migration_*.py`). Step 8 adds
the proof over the whole train, on a database holding pre-train data: the upgrade fixture of
ADR 0116 point 7 (organisations, services with closures, bookings in every status with one
purged, pending jobs, a value of every class C and D setting, an identity provider, an API
client, audit events), taken back to 0108.

| Claim | Proof |
|---|---|
| Fresh database: every migration up, down to base and up again | `tests/test_migrations.py::test_upgrade_and_downgrade_on_fresh_database` |
| The pre-train database holds every kind of row | `tests/test_upgrade_train.py::test_the_pre_train_fixture_holds_every_kind_of_row` |
| Upgrade 0108 to head: every pre-train value unchanged; personal data on live bookings only; every pending job claimable; every stored setting usable | `tests/test_upgrade_train.py::test_upgrading_keeps_every_pre_train_value_and_the_matrix_invariants` |
| Downgrade head to 0108 gives the pre-train database back, value for value; upgrading again gives the same backfill | `tests/test_upgrade_train.py::test_downgrading_gives_the_pre_train_database_back_and_upgrading_again_the_same_backfill` |
| Only `bookings` and `slots` of the pre-train tables change, and only by nullable or defaulted columns; no pre-train column changes type or becomes stricter | `tests/test_upgrade_train.py::test_the_train_changes_only_bookings_and_slots_of_the_pre_train_tables_and_only_additively` |
| N-1 writer: a slot, booking and job written with pre-train columns only is stored, read and claimed by the current code | `tests/test_upgrade_train.py::test_the_previous_image_writes_and_the_current_code_reads_and_claims_it` |
| N-1 writer: the legacy slot index is exactly as strict for the previous image | `tests/test_migration_pool_staffing.py::test_the_previous_image_still_writes_and_is_held_by_the_same_rules` |
| A service an older image created during a rollback gets its pool and flows once, going forward again | `tests/adapters/db/test_default_flows_contract.py::test_the_repair_gives_a_group_of_an_older_image_its_rows_once` |
| Downgrade refuses while a pool slot exists (0111) | `tests/test_migration_pool_staffing.py::test_downgrade_refuses_while_a_slot_carries_a_pool` |
| Downgrade refuses while a request-level record holds personal data (0113) | `tests/test_migration_service_requests.py::test_downgrade_refuses_while_a_record_holds_personal_data` |
| Downgrade refuses while a contact-center attempt exists (0114) | `tests/test_migration_work_item_channel.py::test_downgrade_refuses_while_an_attempt_exists_and_restores_the_states` |
| Downgrade refuses while an immediate request exists (0115) | `tests/test_migration_immediate_intake.py::test_downgrade_refuses_while_immediate_work_exists_and_drops_the_columns` |
| A refused downgrade stops at the refusing step, keeps what that step guards, and goes up again with every pre-train value | `tests/test_upgrade_train.py::test_a_refused_downgrade_stops_at_the_refusing_step_keeps_what_it_guards_and_goes_up_again` |

**What the train adds that the previous image does not know, and why that is safe while the
gate is shut.** No job kind, booking status, event kind or settings key that the previous image
reads changes meaning; the train adds no job kind at all (the watchdog, the reconciliation and
the deletion of request-level records run in the worker's rounds and the nightly sweep). New
settings keys are stored values the previous image never looks up (the upgrade check lists them
as unknown, not as unusable). The records the previous image would miscount (a pool slot carries
its service's `group_id`; an immediate request occupies a unit of a pool) are written only by
gated paths.

**Findings of step 8** (each is a row of the gate checklist):

- *The released-image proof cannot run yet.* ADR 0121 point 9 asks for the previous supported
  image on the expanded database after the new image has written new records, including booking
  and claiming. The upgrade matrix of ADR 0116 (`scripts/upgrade-matrix.sh`) does this with
  released images and their fixtures, but no release carries a fixture yet (v0.5.1 has none;
  the first is v0.6.0), so the CI job has nothing to run. The tests above prove the same at the
  schema level with the current code. The proof with real images is possible from the first
  minor after 0.6.0, which is also the earliest image that may open the gate (ADR 0122,
  consequences).
- *A refused downgrade is not atomic on SQLite.* The migration run is one transaction, but
  SQLite's driver runs schema statements outside it: a refusal at 0111 leaves the database at
  0111 with 0112 to 0115 already undone (their own guards had passed). Nothing a guard protects
  is lost, and `alembic upgrade head` brings the schema back; what the undone steps dropped
  without a guard is configuration (skill contact-center codes, `max_waiting`) and check-in
  times. The runbook says to export the configuration package first and to run the downgrade
  only as the fallback.
- *The OpenAPI document is not diffed against a stored copy.* CLAUDE.md asks for it; today the
  additive `flow` is held by the test above that no `/api/v1` schema requires it.
