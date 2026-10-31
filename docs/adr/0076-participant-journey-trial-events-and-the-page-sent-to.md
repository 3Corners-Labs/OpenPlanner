# 0076. The participant's journey on the timeline: trial call events and the page sent to

Status: accepted (2026-10-30), requested by the product owner: follow the journey of the
participant as far as possible, and be able to troubleshoot it. Extends ADR 0010 (timeline),
ADR 0068 (troubleshooting per booking), ADR 0071 (trial call) and ADR 0075 (the page the
join link opens). Partly built: step 1 is merged (#171), steps 2 and 3 are planned;
see *Build status*.

## Context

The booking page shows a call timeline (ADR 0010), the booking's jobs and the requests made
to Webex (ADR 0068). The product owner asked to follow the participant's journey from booking
to call, on a platform that is not specific to one sector, and to be able to troubleshoot it.
Looking at what is recorded, three gaps showed (2026-10-30):

1. **The trial call is blind.** Webex reports who joins and leaves a meeting, and OpenPlanner
   receives these events for its host accounts. They were matched on the appointment's own
   meeting only. The trial call (ADR 0071) is a meeting of its own, so its events were
   dropped as unknown: the timeline shows *trial opened* and *link followed*, and nothing
   about whether anyone got in. On the test site a guest in the trial call waited in the
   lobby for a host who never comes (ADR 0075), and the booking page could not show it.
2. **The click does not say which page the participant was sent to.** Since ADR 0075 that is
   Webex's web app or Webex's own choice page. A report such as "I saw the app question"
   cannot be told from a link that lost its parameter.
3. **There is no single journey.** Booking, confirmation, changes, mails, the trial call and
   the call are spread over the timeline, the jobs list and the audit log. This ADR records
   the gap; it does not close it yet.

Constraint (CLAUDE.md, ADR 0010): no names, addresses or IP addresses of participants; only
ids, counts, flags and times.

## Decision

1. **Webex events of the trial meeting reach the timeline.** An event for a meeting that is
   not a booking's own meeting is matched on the booking's open trial meeting
   (`get_booking_by_trial_meeting`). A participant that joins is recorded as `trial_entered`,
   one that leaves as `trial_left`, both through the same deduplication as every Webex event.
   The meeting's `started` and `ended` are ignored (outcome `ignored`): our own jobs open and
   delete the meeting, and a late event of a deleted trial meeting is unknown like any other.
2. **The appointment's call status is never touched by the trial call.** The trial kinds change
   no participant count, no *host joined* flag and no meeting state; only `updated_at` moves,
   as for the other trial events.
3. **A host in the trial call is noted.** A join by a host or cohost is `trial_entered` with
   detail `host`: nobody from the team is meant to be in the trial call (ADR 0071 point 2),
   so it is worth seeing.
4. **The click names the page.** `join_clicked` and `trial_joined` carry the participant's
   `join_client` (`browser` or `choice`, ADR 0075) as their detail. It is a fixed word, never
   an address, and shows next to the event on the booking page like the other codes.
5. **Not decided here, and not built:** a merged chronological journey on the booking page
   built from what is stored (booking events, jobs, requests and the booking's audit events),
   with a filter *problems only*, and a derived signal *link followed, nobody entered after a
   few minutes*. Measuring on the participant's device (scripts, device data) is out: it
   would contradict the rule of no tracking and no personal data.

## Settings

None. The ignored event kinds and the detail words are fixed. The signal of decision 5 will
need a period (class C or D) when it is built; it is listed there, not guessed here.

**Fixed on purpose:** which Webex events are recorded for the trial meeting follows from what
the webhooks deliver (ADR 0017); the detail word of decision 4 is the value of the group's
setting and has no bounds of its own.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Trial meeting events matched and recorded (`trial_entered`, `trial_left`), `ignored` outcome, the page named on `join_clicked` and `trial_joined`, texts in nl and en, `docs/webhooks.md` | #171 | merged |
| 2 | The signal *followed, nobody entered*, as a derived flag on the booking page | | planned |
| 3 | The merged journey on the booking page, with *problems only* | | planned |

## Consequences

- No migration: `booking_events.kind` is a free string, and the new kinds fit its length.
- `docs/webhooks.md` explains the trial meeting's events; `docs/dataflow.md` and
  `docs/threat-model.md` are unchanged (no new endpoint, no new personal data: the events
  carry the same ids and flags as the appointment's).
- The trial call's webhook events share the 30 days of remembered keys (ADR 0010) and the
  retention of the timeline of the booking they belong to.
- Whoever opens a booking after a trial call now sees whether anyone entered. A click without
  an entry afterwards is the first hint of a guest stuck in a lobby or on an app question;
  step 2 makes that explicit.
