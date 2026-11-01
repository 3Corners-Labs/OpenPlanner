# 0021. Ending the work item of a cancelled booking

Status: accepted (2026-10-25). Supersedes ADR 0018 point 5.
Note (2026-11-01 review): ADR 0069 says it amends "the read-only rule of ADR 0017 and 0021";
this ADR states no such rule (the lock is `Group.change_deadline`). The confirmation with the
Cisco beta team named in the Consequences is not recorded; ADR 0082 §2 relies on End Task.

## Context

ADR 0018 point 5 left open whether a Service App can end a work item, and chose a Task Messages
update as the stand-in. Checked again on 2026-10-25 against the developer portal (Work Items
guide, Tasks Call Control reference and the integration scope list):

- **End Task** (`POST /v1/tasks/{taskId}/end`) "requires one of the following scopes
  'cjp:user' or 'cloud-contact-center:pod_conv'". The scope list offers `cjp:user` to Service
  Apps as well as integrations ("Access agent and supervisor actions that can be performed from
  the Agent/Supervisor Desktop"); `cloud-contact-center:pod_conv` is offered to integrations
  only. The Work Items guide itself tells middleware to call End Task to close a stale task
  (`CONVERSATION_ALREADY_OPEN` recovery). Whether End Task accepts a Service App token for a
  task no agent has accepted is not documented; the beta team has not confirmed it.
- **Append Task Message** (`POST /v2/tasks/{taskId}/messages`, beta) needs `cjp:task_write`
  only, the scope OpenPlanner already has.
- Measured on 2026-10-25 with `wxcc-check` in the product owner's organisation: End Task
  accepts the Service App's token (`202` for a task id that does not exist; the request is
  processed asynchronously), and the empty Create Task v2 and Task Messages v2 calls get `400`
  (authorised, invalid body). For work items it appends a snapshot of the work
  item data; it does not end the task.
- A task can be cancelled after its work item exists when a booking is cancelled or moved
  after dispatch (for example after a group shortened its dispatch lead), and, once the
  citizen can stop waiting, for direct calls (ADR 0019 point 4).

## Decision

The work item API is v2 (beta); **v2 is primary**, v1 is used only where v2 has no
equivalent, as a secondary step (product owner, 2026-10-25).

1. Cancelling (or moving) a booking plans an `end_wxcc_task` job next to `delete_video_room`.
   It carries the booking id only. It does nothing without a task id, or once WxCC reported
   the task ended or failed.
2. **Primary, v2:** the job appends a **Task Messages v2** snapshot with only
   `bookingReference` and `kind: cancelled` (`cjp:task_write`, the scope OpenPlanner already
   has), so the agent sees that the booking was cancelled, without the first name or the
   meeting link. Recorded as `task_marked_cancelled` (task state *Taak geannuleerd*). A retry
   after a successful mark does not mark again.
3. **Secondary, v1:** the job then calls **End Task v1** (there is no v2 End Task); the
   Service App requests `cjp:user` for it. Success is recorded as `task_end_requested`;
   WxCC's `task:ended` event follows through the webhook. A refusal (`rejected_403` without
   the scope, or any other non-retryable code) is recorded as **`end_not_possible`** with the
   code; the v2 mark stands.
4. A refused v2 mark is an `integration_error` on the timeline, and End Task is still tried.
   Transient failures (timeouts, 429, 5xx) are retried like every call job. A final failure is
   an `integration_error`, never a failed task.
5. The admin booking detail explains an `end_not_possible`: the task may still be open and an
   agent or supervisor ends it in Agent Desktop. `python -m app.cli wxcc-check` probes the v2
   calls as required and the v1 calls (End Task with a task id that cannot exist, event types,
   task search) as secondary: they print `WARN` and do not fail the check.

## Consequences

- The Control Hub checklist (runbook) adds `cjp:user` to the Service App as optional. Without
  it, cancellation still reaches WxCC: the task is marked cancelled through v2.
- **Gap:** without `cjp:user`, or while End Task refuses Service App tokens, a cancelled work
  item stays in the queue until an agent picks it up and ends it. The WxCC flow can shorten
  that by acting on `kind = cancelled` (for example a short queue timeout). To be confirmed
  with the Cisco beta team, after which this ADR is superseded or confirmed.
- The work item asset's `kind` field now takes `scheduled`, `direct` or `cancelled`.
- Direct calls: the waiting page lets the citizen stop waiting (ADR 0019 point 4); it reuses
  this job unchanged. If an agent already took the call, the meeting is kept for the agent
  while the work item still ends.
