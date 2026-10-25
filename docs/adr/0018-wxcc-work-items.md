# 0018. Dispatch to WxCC as work items (Create Task API v2)

Status: accepted (2026-10-23). Supersedes ADR 0007 points 1–2. Point 5 superseded by ADR 0020.

## Context

ADR 0007 chose the WxCC Tasks API and a queue ID per group. The organisation has Work Items in
beta. Verified against the developer guide (2026-10-23): a work item is created with the Create
Task API v2 (`channelType: workItem`) on a **business address** of a Work Item asset and a
**channel** name, both configured in Control Hub. The asset defines a schema for
`workItemData` (at least one required and one viewable field, at most 50 fields, values up to
1024 characters). The flow behind the entry point chooses the queue; `flowSettings` pass
variables to it. Agents see viewable fields read-only on Agent Desktop; a Screen Pop activity can
open a URL. Updates go through the Task Messages API, cancellation through the End Task API.
Events come through the Subscriptions API (`task:new`, `task:connected`, `task:ended`,
`task:failed`, `task-message:*`), signed with HMAC-SHA256 (`X-WebexCC-Signature`) and
timestamped (`X-WebExCC-Timestamp`, equal to `comciscotimestamp`). The Service App needs
`cjp:task_write` and `cjp:task_read`.

## Decision

1. Per group the mapping holds the business address, the channel name and a routing value
   passed as `flowSettings.openplannerGroup` (default: the group slug). The WxCC flow routes on
   it to the right queue, so one asset and flow can serve many groups.
2. `workItemData` fields (configured identically in the asset schema, runbook):
   `bookingReference` (required, viewable), `firstName` (viewable), `startTime` (viewable),
   `groupName` (viewable), `meetingLink` (viewable; the meeting's `webLink`) and `kind`
   (`scheduled` or `direct`). Nothing else: no email, phone or full name (CLAUDE.md).
   `origin.id` is the booking reference; `origin.name` is the first name.
3. The returned task id is stored on the booking. Lifecycle events update the booking's
   timeline and the live status; `task:failed` reasons are shown to admins and retried where
   transient.
4. Webhooks are verified (signature with the subscription secret, header and body timestamp
   equal, within 300 s) before anything is read.

5. **Open with the Cisco beta team:** End Task (`POST /v1/tasks/{taskId}/end`) documents agent
   scopes (`cjp:user`, `cloud-contact-center:pod_conv`). Until a Service App is confirmed to end
   a work item, OpenPlanner marks a cancelled task with a Task Messages update
   (`status: cancelled`) that the flow or agent can act on.

## Consequences

- Subscriptions accept only HTTPS on port 443 with a public domain: local development uses a
  tunnel or the staging host to receive WxCC events.
- WP7b's `wxcc_queue_id` field is replaced by the three mapping fields (migration).
- The customer's admin configures channel, asset, schema, entry point, flow and queues; the
  runbook lists the exact values.
- Work Items is beta; the adapter keeps the contract in recorded fixtures so a change shows
  up as a failing contract test.
