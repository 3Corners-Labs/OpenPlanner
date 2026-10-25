# Integrations: Webex Meetings and Webex Contact Center

Contracts verified on 2026-10-23 against developer.webex.com (Meetings API, WxCC Work Items
guide and the embedded OpenAPI specs of Create Task v2, End Task and Subscriptions v2).
ADR 0017 (video), ADR 0018 (work items), ADR 0019 (direct call).

## Authentication

One Webex **Service App**, authorised by an administrator in Control Hub, provides an access
token (valid 14 days) and a refresh token (valid 90 days). OpenPlanner refreshes with
`POST https://webexapis.com/v1/access_token` (`grant_type=refresh_token`) before expiry and
stores the tokens encrypted (ADR 0008). Scopes:

| Scope | For |
|---|---|
| `meeting:admin_schedule_write`, `meeting:admin_schedule_read` | create and delete meetings on behalf of the group's host |
| `meeting:admin_participants_read` | live status (participants) |
| read scope of `meetings` and `meetingParticipants` at admin level (org-owned webhooks; exact scope names to be confirmed when registering) | live status events |
| `cjp:task_write`, `cjp:task_read` | Create Task v2, Task Messages |
| `cjp:user` | End Task for cancelled bookings (ADR 0020); without it the task is only marked cancelled |
| `cjp:config_read`, `cjp:config_write` | register and list the WxCC subscription, event types, task search in `wxcc-check` (administrator role) |

## Outbound: Webex Meetings

`POST https://webexapis.com/v1/meetings`, on behalf of the group's host:

```json
{
  "title": "Videogesprek Demo: videogesprek",
  "start": "2026-10-26T09:00:00+01:00",
  "end": "2026-10-26T09:30:00+01:00",
  "timezone": "Europe/Amsterdam",
  "hostEmail": "host-demo@example.nl",
  "allowAnyUserToBeCoHost": true,
  "enabledJoinBeforeHost": false,
  "unlockedMeetingJoinSecurity": "allowJoinWithLobby",
  "sendEmail": false,
  "meetingOptions": {"enabledChat": true, "enabledVideo": true, "enabledFileTransfer": false},
  "attendeePrivileges": {"enabledChatHost": true, "enabledChatOtherParticipants": true}
}
```

The title never contains the citizen's name. OpenPlanner keeps `id` (as `video_room_id`) and
`webLink` (as `guest_join_url` and `host_join_url`; one link, the role follows the signed-in
account). Cancelling deletes the meeting: `DELETE /v1/meetings/{id}?hostEmail=…`.

## Outbound: WxCC work item

`POST https://api.wxcc-<region>.cisco.com/v2/tasks` (beta). Response `201` with `data.id` (the
task id, stored as `wxcc_task_id`).

```json
{
  "origin": {"id": "OP-000123", "name": "Ada"},
  "destination": {"id": "<business address of the work item asset>", "type": "businessAddress"},
  "channelType": "workItem",
  "channel": "<work item channel name>",
  "flowSettings": {"openplannerGroup": "<routing value of the group>"},
  "channelParams": {
    "type": "work-item-form",
    "message": {
      "aliasId": "<uuid>",
      "workItemData": {
        "bookingReference": "OP-000123",
        "firstName": "Ada",
        "startTime": "26 okt 2026, 09:00",
        "groupName": "Demo: videogesprek",
        "meetingLink": "https://<site>.webex.com/<…>",
        "kind": "scheduled"
      },
      "timestamp": 1792998000000
    }
  }
}
```

Limits: `workItemData` ≤ 50 keys (≤ 100 bytes), values ≤ 1024 bytes; `flowSettings` ≤ 15
tuples (key ≤ 200, value ≤ 512 bytes). Only first name and the meeting link leave OpenPlanner
(CLAUDE.md).

**Ending a cancelled booking's task** (ADR 0020). First End Task,
`POST /v1/tasks/{taskId}/end` without a body (`cjp:user`; `202` on success). If WxCC refuses,
the timeline records `end_not_possible` with the code and OpenPlanner appends a Task Messages
snapshot, `POST /v2/tasks/{taskId}/messages` (`cjp:task_write`, beta, `202`):

```json
{
  "mediaType": "workItem",
  "channelParams": {
    "type": "work-item-form",
    "message": {
      "aliasId": "<uuid>",
      "workItemData": {"bookingReference": "OP-000123", "kind": "cancelled"},
      "timestamp": 1792998000000
    }
  }
}
```

Only the booking reference goes out: no first name, no meeting link.

## Webhook addresses

Both platforms call `<WEBHOOK_BASE_URL>/webhooks/webex` and `<WEBHOOK_BASE_URL>/webhooks/wxcc`.
`WEBHOOK_BASE_URL` is optional and defaults to `PUBLIC_BASE_URL`; it exists so that an https
tunnel or staging host can receive webhooks while the admin login (OIDC redirect) and the
links in messages stay on `PUBLIC_BASE_URL`. It must be https in production, and
`register-webhooks` refuses anything but https. **Koppelingen** shows the addresses in use.

## Inbound: Webex webhooks (`POST /webhooks/webex`)

Registered once per installation (org-owned) for `meetings` (`started`, `ended`) and
`meetingParticipants` (`joined`, `left`) with a secret. Verification: HMAC-SHA1 of the raw body
with the secret, hex, compared in constant time with `X-Spark-Signature`. Events for unknown
meetings are ignored. Webex signs no event time, so replays are caught by **deduplication**:
each event's key, a SHA-256 of `resource`, `event`, `data.id` and `data.joinedTime`/`leftTime`
(a participant keeps its `data.id` across leave and rejoin), is stored in `webhook_events` in
the same transaction as the event; a key seen before is acknowledged (204) and ignored. Keys
are kept 30 days and only for OpenPlanner's own meetings. The payload's meeting id can be an instance id
(`<meetingId>_I_<…>`); it is matched on the series id. Participant names and emails are never
stored: only counts, whether a host/cohost joined, and times (ADR 0010).

## Inbound: WxCC subscription (`POST /webhooks/wxcc`)

Registered with `POST /v2/subscriptions` (`name`, `eventTypes`, `destinationUrl`, `secret`).
`destinationUrl` must be HTTPS on port 443 with a public domain: local development needs a
tunnel or a staging host. Event types: `task:new`, `task:connected`, `task:ended`,
`task:failed`, `task-message:appended`, `task-message:append-failed`.

Verification (before reading anything else):

1. `X-WebexCC-Signature` equals hex HMAC-SHA256 of the raw body with the subscription secret
   (constant-time compare).
2. `X-WebExCC-Timestamp` equals the body's `comciscotimestamp`.
3. |now − timestamp| ≤ 300 s.

Correlation is on `data.taskId`. `task:connected` carries no agent identity, which is why the
agent's host role comes from `allowAnyUserToBeCoHost` (ADR 0017). `task:failed` carries
`reason` (for example `ENTRY_POINT_NOT_FOUND`, `ORG_DIGITAL_CONTACT_LIMIT_EXCEEDED`), shown to
admins on the booking's timeline.
