# 0017. Video calls through the Webex Meetings API

Status: accepted (2026-10-23). Settles open point O1 for now: Meetings API instead of Instant Connect.
Point 3's join target amended by ADR 0075 (`webLink` with `launchApp=true` per group). Amended
by ADR 0072 §7 (whole seconds; a start that has passed becomes the moment of the request).
Note (2026-11-01 review): ADR 0069 says it amends "the read-only rule of ADR 0017 and 0021";
this ADR states no such rule (the lock is `Group.change_deadline`, ADR 0043 and 0069).

## Context

Each appointment needs a video meeting in which the agent has host functions and the citizen
joins as a participant from a browser, without an account. The agent may use the Webex app.
More participants may join (an interpreter, a colleague), so it is a regular meeting. The
product owner wants to try the regular Webex Meetings API first.

What the API offers (verified 2026-10-23): a Service App schedules meetings on behalf of a
licensed host (`hostEmail`, scope `meeting:admin_schedule_write`). Meeting options include
`allowAnyUserToBeCoHost`, `enabledJoinBeforeHost`, `unlockedMeetingJoinSecurity`, chat settings
(`meetingOptions.enabledChat`, `attendeePrivileges.enabledChat*`) and `templateId`. The
meeting's `webLink` does not expire. Direct join links from `POST /meetings/join` expire after
at most 60 minutes and need an email address to carry a display name. Webhooks report
`meetings` started/ended and `meetingParticipants` joined/left. Background blur and approved
backgrounds (for example a central government background) are organisation or site settings in
Control Hub, not per meeting.

## Decision

1. **Host and roles.** The meeting is scheduled by a Service App on behalf of a licensed host
   account configured per group. `allowAnyUserToBeCoHost` is on: every signed-in user of the
   organisation (the agent, colleagues) becomes cohost on joining; the citizen joins as a guest
   participant. WxCC task events carry no agent identity, so naming one agent as cohost is not
   possible (choice A1).
2. **Lobby.** Guests wait in the lobby until a host or cohost admits them; nobody else can join
   the citizen's meeting first (choice D1).
3. **Citizen's link.** The email links to an OpenPlanner join page (`/b/{token}/join`), not to
   Webex directly. The page checks the time window and then links to the meeting's `webLink`.
   Links in emails never expire, no personal data goes to Webex for the link, and the page
   records that the citizen opened it (choice B1). Direct join links with a pre-filled name are
   not used: they would require sending the citizen's email address to Webex.
4. **Chat.** Chat inside the meeting is on (host and participants). A separate chat outside the
   meeting is not part of Webex Meetings; a WxCC digital channel could provide it later.
5. **Defaults for video.** Blur by default and the approved backgrounds are configured in
   Control Hub (runbook checklist). A per-group meeting template (`templateId`) can carry
   further defaults later.
6. **Status.** Webhooks keep the live status (ADR 0010) and the booking's timeline current.
   They are verified with the webhook secret (`X-Spark-Signature`, HMAC-SHA1) and ignored for
   unknown meetings. Only aggregates are stored.
7. **Tokens.** The Service App's access token (14 days) is refreshed with its refresh token
   (90 days). Tokens are integration secrets (ADR 0008): encrypted at rest, never logged.

## Consequences

- The meeting exists from booking on; cancelling or moving a booking deletes or replaces it.
- Mobile browsers may push the Webex app; this is tested with the customer's site settings.
- A licensed host account per group (or one shared) is needed, and Control Hub settings for
  guests, lobby, blur and backgrounds.
