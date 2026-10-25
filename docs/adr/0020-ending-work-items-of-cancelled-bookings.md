# 0020. Ending the work item of a cancelled booking

Status: accepted (2026-10-25). Supersedes ADR 0018 point 5.

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
  only, the scope OpenPlanner already has. For work items it appends a snapshot of the work
  item data; it does not end the task.
- A task can be cancelled after its work item exists when a booking is cancelled or moved
  after dispatch (for example after a group shortened its dispatch lead), and, once the
  citizen can stop waiting, for direct calls (ADR 0019 point 4).

## Decision

1. Cancelling (or moving) a booking plans an `end_wxcc_task` job next to `delete_video_room`.
   It carries the booking id only. It does nothing without a task id, or once WxCC reported
   the task ended or failed, or after it was marked cancelled.
2. The job first calls **End Task**. The Service App requests `cjp:user` for it. Success is
   recorded as `task_end_requested`; WxCC's `task:ended` event follows through the webhook.
3. If WxCC refuses (`rejected_403` without the scope, or any other non-retryable code), the
   refusal is recorded as **`end_not_possible`** with the code. OpenPlanner then appends a
   **Task Messages** snapshot with only `bookingReference` and `kind: cancelled`, so the agent
   sees that the booking was cancelled, without the first name or the meeting link, and
   records `task_marked_cancelled` (task state *Taak geannuleerd*).
4. If the message is refused too, both reasons go on the timeline (`end_not_possible`,
   `integration_error`). Transient failures (timeouts, 429, 5xx) are retried like every call
   job. A final failure is an `integration_error`, never a failed task.
5. The admin booking detail explains an `end_not_possible`: the task may still be open and an
   agent or supervisor ends it in Agent Desktop. `python -m app.cli wxcc-check` probes End Task
   with a task id that cannot exist (404 means the scope works, 403 means it does not).

## Consequences

- The Control Hub checklist (runbook) adds `cjp:user` to the Service App. Without it,
  cancellation still works: OpenPlanner only marks the task cancelled.
- **Gap:** without `cjp:user`, or while End Task refuses Service App tokens, a cancelled work
  item stays in the queue until an agent picks it up and ends it. The WxCC flow can shorten
  that by acting on `kind = cancelled` (for example a short queue timeout). To be confirmed
  with the Cisco beta team, after which this ADR is superseded or confirmed.
- The work item asset's `kind` field now takes `scheduled`, `direct` or `cancelled`.
- Direct calls have no citizen-side cancel yet; once the waiting page gets one (with the
  citizen pages, WP3b), it reuses this job unchanged.
