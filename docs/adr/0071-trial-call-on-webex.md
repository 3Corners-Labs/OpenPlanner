# 0071. A trial call on Webex ahead of the appointment

Status: accepted (2026-10-30), requested by the product owner. Extends ADR 0017 (video through
the Webex Meetings API). Settings per ADR 0042. Touches open point O8 (accessibility of the
video call). Note (2026-11-01 review): the test site ignores `TRIAL_OPTIONS` (ADR 0075 finding
5), so there the citizen waits in the lobby and nobody comes; the group check warns
`webex.trial_lobby` (ADR 0075 §5, #171). Whether the production site allows entry before the
host is open on the go-live checklist.

## Context

For many citizens the video call is a first: they are nervous, and they want to know in good
time that camera, microphone, speakers and browser work. The product owner asked for a way to
try this well ahead of the appointment. A first sketch tested the devices in the browser on our
own domain. The product owner rejected it: the test must be done on Webex itself. A lookalike
check can pass while the real call fails (Webex site, Webex client in the browser, its
permission prompts, its network path), and the citizen would then start the appointment on a
false assurance.

What the code and Webex give us:

- A booking's meeting is scheduled by the group's licensed host (ADR 0017) on the organisation's
  Webex site, with fixed options (`MEETING_OPTIONS`: lobby for guests, chat on).
- The Meetings API accepts `enabledJoinBeforeHost`, `enableConnectAudioBeforeHost` and
  `unlockedMeetingJoinSecurity` on create (ADR 0045 lists them). With them a participant enters
  a scheduled meeting alone, without a host.
- The group check already schedules and deletes a probe meeting exactly as a booking does
  (ADR 0065), so a refusal by Webex shows up there first.
- The meeting link gives access to a room, so like the appointment's link it is personal data
  while the booking lives (CLAUDE.md privacy rules).

## Decision

1. **The trial call is a real Webex meeting.** The citizen opens it from `/b/{token}/test`. The
   service schedules it for the group's host with the same adapter and the same body as the
   booking's meeting. Only three options differ: entry before the host, audio before the host,
   and no lobby (`TRIAL_OPTIONS` in the adapter). Nothing is built in our own page: the citizen
   sees Webex's own pre-join screen, device pickers and permission prompts. There is no
   browser-side check, no recording of the result and no link to Webex's generic test page.
2. **No one from the team joins.** The host account is never present and no work item is
   created. The page says so, since a citizen might otherwise wait for someone.
3. **Open on request, for a limited time.** The meeting is created when the citizen presses the
   button, starts at that minute and lasts `trial_call_minutes`. While it is open the page
   offers the link and reuses the same meeting. After that it is deleted by the job
   `delete_trial_call`, planned five minutes after it closes (`CLEANUP_DELAY`), so nobody is cut
   off. Opening again creates a new meeting.
4. **Only for a confirmed booking, before its start.** A booking awaiting confirmation,
   suspended, cancelled or started has none (`trial_state`). A group needs a Webex host and the
   switch on.
5. **At most `trial_calls_per_booking` trial calls per booking.** Each one is a meeting at
   Webex that a public page can create, so the number is bounded per booking (token). The page
   has the same per-IP rate limit as other reads, since the booking's own limit is the real
   protection and booking creation's pool must not be used up by trials.
6. **Storage.** `bookings` gets `trial_room_id`, `trial_join_url` (PII, purged with the
   contact), `trial_expires_at` and `trial_count`. A cancelled or moved booking plans
   `delete_trial_call` with its other cleanup jobs (`plan_for_cancellation`), so its meeting is
   removed at once. The job does nothing when there is none.
7. **A race is harmless.** The meeting is scheduled outside the write lock. When two requests
   race, the second finds the first stored and deletes its own meeting again.
8. **A failure never touches the appointment.** If Webex refuses or is unreachable, the
   timeline gets `trial_failed` with Webex's reason (ADR 0065), the page says it does not work
   now and does not count the attempt. The booking is unchanged.
9. **The group check covers it.** After the booking probe, "Koppeling controleren" schedules a
   trial-call probe with the same options and deletes it. A refusal is a warning
   (`webex.trial_refused`, with Webex's reason): a site that forbids entry before the host
   leaves citizens without a trial call, but not without their appointment.
10. **Where citizens find it.** The confirmation and reminder mails (a link next to the join
    link), the booking page and the join page link to it. Texts are i18n keys (`trial.*`,
    `message.trial*`) in nl and en.
11. **Timeline.** `trial_created`, `trial_joined` (the click into the meeting), `trial_failed`
    and `trial_deleted` are shown on the booking's timeline. They carry no personal data.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `trial_call_enabled` | D organisation (per group) | on | on/off | `group.edit` | Not a protection; off hides every link. Needs a Webex host. |
| `trial_call_minutes` | D organisation (per group) | 30 | 10–60 | `group.edit` | How long one trial call stays open. |
| `trial_calls_per_booking` | D organisation (per group) | 3 | 1–5 | `group.edit` | Bounds the meetings one booking can create at Webex. |

**Fixed on purpose:** the five-minute cleanup delay after a trial call closes (so nobody is cut
off; no reason to tune it); the entry options of the trial meeting (they are what makes it a
trial); that the trial uses the group's own Webex host and site (the point of this ADR); that
the page offers at most one open meeting at a time.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Trial call on Webex: `trial_call*`, trial pages, `delete_trial_call` job, probe in the group check (migration 0063) | #154 | merged |

(State as checked against the code on 2026-10-30; the overview of every ADR is `docs/adr-status.md`.)

## Consequences

- A Webex site that forbids entry before the host cannot offer the trial call. The group check
  tells the admin; the runbook says where to allow it.
- Trial meetings show in the host's Webex calendar for the time they exist. They are named
  "Proefgesprek {group}" and carry no citizen data.
- A trial meeting that is not deleted (Webex down for longer than the job's retries) stays an
  empty room that ends with its scheduled time; the timeline shows the failed delete.
- The PII list in CLAUDE.md and `docs/dataflow.md` gain the trial link. The threat model gets
  the abuse case "creating Webex meetings through the trial page" with its tests.
- Not verified against the customer's Webex tenant here: that the host's site allows entry
  before the host, and what the participant sees on the pre-join screen. The first run of
  "Koppeling controleren" on docker01 answers the first; a manual run through the trial call on
  docker01 answers the second (the runbook lists both).

## Amendments to earlier decisions

- **ADR 0063 and CLAUDE.md, privacy rules.** The one list of personal-data fields gains
  `trial_join_url` (sensitive, on `Booking` only). It is masked in logs, cleared by `purge_pii`
  (`purge_personal_data`) and part of the *Data fields* card; the privacy sweep plants a marker
  for it and checks that no admin page leaks it.
