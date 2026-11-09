# Reporting on use

Generated from `app/domain/usage.py` and `app/domain/privacy.py` by
`python -m app.cli reporting-doc > docs/reporting.md`; do not edit by hand. CI fails when this
file differs from the code. The decision is
[ADR 0117](adr/0117-reporting-on-use-from-a-store-that-holds-no-personal-data.md).

The statistics hold no personal data. They are counts and sums per hour and per cut, never one
line per booking, and every read hides a cell below the threshold (never below
3). Metric definitions are version 1; a change of a definition
or a bucket bound needs a new version.

## Metrics

| ID | Metric | Definition | Facts | Adds up as |
|---|---|---|---|---|
| U1 | Bookings created | Count of `booking.created` that are no move, by creation hour. | `booking.created` | count |
| U2 | Outcomes | Count per final outcome: completed, no show, cancelled by participant, by planner, by closure, by system, expired unconfirmed. | `booking.outcome`, `booking.cancelled`, `booking.confirm_expired` | count |
| U3 | No-show rate | `no_show / (completed + no_show)` in the cohort; cancelled and suspended excluded. | `booking.outcome` | count |
| U4 | Cancellation rate | Cancelled by participant over bookings created in the cohort, excluding moves. | `booking.cancelled`, `booking.created` | count |
| U5 | Late cancellation | Share of cancellations whose lead time is inside the group's `participant_change_lead_minutes` (ADR 0069). | `booking.cancelled` | count |
| U6 | Lead time | `slot.start - created_at`, in the duration buckets, per created booking. | `booking.created` | sum |
| U7 | Reschedules | Count of `booking.moved` by actor class and reason; rate `moved / bookings in the cohort`. | `booking.moved` | count |
| U8 | Rebooking funnel | Proposed, accepted, expired; conversion `accepted / proposed`; time to accept in buckets; split by planner-asked and assistant-proposed (ADR 0044, 0057). | `rebook.proposed`, `rebook.accepted`, `rebook.expired` | count |
| U9 | Confirmation conversion | `confirmed / (confirmed + expired unconfirmed)`; time to confirm in buckets (ADR 0056). | `booking.confirmed`, `booking.confirm_expired` | count |
| U10 | Closure impact | Bookings suspended, resumed, cancelled per closure; no closure id is stored, only the day and counts (ADR 0024). | `booking.suspended`, `booking.resumed` | count |
| U11 | Participants per call | Sum and mean of `participants` of finished calls. | `call.ended` | sum |
| Q1 | Queue-method mix | Share of bookings by dispatch kind (contact center, own queue, calendar invite) and booking kind (scheduled, direct, including start now; ADR 0019, 0072, 0043). | `booking.created` | count |
| Q2 | Task wait | `connected_at - task_created_at` of a call dispatched to the contact center or the own queue; buckets, exact mean. | `call.connected` | sum |
| Q3 | Host punctuality | `host_joined_at - slot.start`, signed (negative: the host was early); only scheduled calls. | `call.host_joined` | sum |
| Q4 | Participant wait in the meeting | `host_joined_at - max(slot.start, guest_joined_at)` when positive. | `call.guest_waited` | sum |
| Q5 | Call length | `ended_at - started_at` where both are set; buckets, exact mean. | `call.ended` | sum |
| Q6 | Abandoned direct calls | `waiting_left / direct calls created`, with the wait at the moment of leaving in buckets; and the count not picked up within `not_picked_up_minutes`. | `call.created`, `call.waiting_left`, `call.not_picked_up` | count |
| Q7 | Own-queue claim | Share claimed, time from the queue becoming visible to `claimed`, releases per claim. | `queue.claimed`, `queue.released` | count |
| Q8 | Dispatch health | Count of `task_failed`, `meeting_failed` and `integration_error` per day, by a code from a closed list (not the provider's text). | `call.failed` | count |
| Q9 | Direct-call load | Peak concurrent waiting-plus-connected per group and hour (ADR 0072 cap), computed from facts, not from live state. | `call.created`, `call.connected`, `call.ended` | max |
| C1 | Utilisation | `sum(booked) / sum(capacity_snapshot)` over slots that have started; `booked` is confirmed plus completed plus no show, not cancelled and not pending. | `slot.closed` | sum |
| C2 | Fill distribution | Slots by fill: empty, under half, under full, full. | `slot.closed` | count |
| C3 | Idle capacity | `sum(capacity - booked)` over started slots. | `slot.closed` | sum |
| C4 | Busiest hours and days | Bookings by group-local weekday and hour of the slot start, as a table with a heat shade and the number in each cell; derived at render time. | `booking.slotted` | count |
| C5 | Shortfall | Blocks where staffed agents were fewer than bookings (ADR 0044), split accepted and resolved. | `slot.shortfall` | count |
| S1 | Load per team member (team view, off by default) | Calls handled, call minutes, claims, on-time share per `staff_id` (ADR 0117 point 9). | `call.ended`, `queue.claimed` | sum |
| S2 | Team utilisation (team view, off by default) | `handled call seconds / staffed seconds` of the blocks in the group's staffing plan. | `call.ended`, `slot.closed` | sum |
| M1 | Messages | Sent and failed per channel kind (email, sms) and purpose (confirmation, reminder, change, cancellation); failure rate. | `message.sent`, `message.failed` | count |
| M2 | Locale mix | Bookings by locale. | `booking.created` | count |
| T1 | Trial calls | Share of bookings with a trial created; of those, followed the link; of those, entered the meeting (the gap is people stuck in the lobby); failures (ADR 0071, 0076). | `trial.created`, `trial.joined`, `trial.entered`, `trial.failed` | count |

## Facts and the hour they are keyed on

| Fact | Hour from |
|---|---|
| `booking.created` | creation |
| `booking.slotted` | slot_start |
| `booking.confirmed` | event |
| `booking.confirm_expired` | slot_start |
| `booking.cancelled` | slot_start |
| `booking.moved` | event |
| `booking.outcome` | slot_start |
| `booking.suspended` | event |
| `booking.resumed` | event |
| `rebook.proposed` | event |
| `rebook.accepted` | event |
| `rebook.expired` | event |
| `call.created` | event |
| `call.connected` | event |
| `call.host_joined` | event |
| `call.guest_waited` | event |
| `call.ended` | event |
| `call.waiting_left` | event |
| `call.not_picked_up` | slot_start |
| `call.failed` | event |
| `queue.claimed` | event |
| `queue.released` | event |
| `slot.closed` | slot_start |
| `slot.shortfall` | slot_start |
| `message.sent` | event |
| `message.failed` | event |
| `trial.created` | event |
| `trial.joined` | event |
| `trial.entered` | event |
| `trial.failed` | event |

## How a fact becomes a cell

One row per metric a fact kind feeds. The count of a cell is always the number of facts; the figure is what the metric adds up. A word in `reason_code` is the metric's own state, not the fact's reason.

| Fact | Metric | Figure | Cut by | Only when |
|---|---|---|---|---|
| `booking.created` | U1 | count | none | A booking made by a move is no new booking: it feeds no volume metric |
| `booking.created` | U6 | seconds in buckets | none | A booking made by a move is no new booking: it feeds no volume metric |
| `booking.created` | Q1 | count | `booking_kind`, `dispatch_kind` | A booking made by a move is no new booking: it feeds no volume metric |
| `booking.created` | M2 | count | `locale` | A booking made by a move is no new booking: it feeds no volume metric |
| `booking.slotted` | C4 | count | none |  |
| `booking.confirmed` | U9 | seconds in buckets | none |  |
| `booking.confirm_expired` | U2 | count | `outcome` |  |
| `booking.confirm_expired` | U9 | count | `outcome` |  |
| `booking.cancelled` | U2 | count | `outcome` |  |
| `booking.cancelled` | U4 | count | `actor_class` | Only a participant's cancellation feeds the rate and the lateness |
| `booking.cancelled` | U5 | signed seconds in buckets | none | Only a participant's cancellation feeds the rate and the lateness |
| `booking.moved` | U7 | count | `actor_class`, `reason_code` |  |
| `booking.outcome` | U2 | count | `outcome` |  |
| `booking.outcome` | U3 | count | `outcome` |  |
| `booking.suspended` | U10 | count | `actor_class`, reason_code = `suspended` |  |
| `booking.resumed` | U10 | count | `actor_class`, reason_code = `resumed` |  |
| `rebook.proposed` | U8 | count | `actor_class`, reason_code = `proposed` |  |
| `rebook.accepted` | U8 | seconds in buckets | reason_code = `accepted` |  |
| `rebook.expired` | U8 | count | reason_code = `expired` |  |
| `call.created` | Q6 | count | `dispatch_kind`, reason_code = `created` | Only a direct call counts for the waiting and the load (metrics Q6 and Q9) |
| `call.created` | Q9 | The direct calls waiting or connected right after a call was created (metric Q9) | none | A direct call's creation that says how many calls were open (metric Q9) |
| `call.connected` | Q2 | seconds in buckets | `booking_kind`, `dispatch_kind` | The fact has its seconds: a call whose start or end was never seen has none |
| `call.host_joined` | Q3 | signed seconds in buckets | `dispatch_kind` | Only a scheduled call has a start to be punctual or late against (metric Q3) |
| `call.guest_waited` | Q4 | seconds in buckets | `dispatch_kind` | The fact has its seconds: a call whose start or end was never seen has none |
| `call.ended` | Q5 | seconds in buckets | `booking_kind`, `dispatch_kind` | The fact has its seconds: a call whose start or end was never seen has none |
| `call.ended` | U11 | The most people seen in a finished call (metric U11) | `booking_kind` |  |
| `call.waiting_left` | Q6 | seconds in buckets | `dispatch_kind`, `reason_code` | A direct call that was left, with the wait at that moment (metric Q6) |
| `call.not_picked_up` | Q6 | count | `booking_kind`, `dispatch_kind`, reason_code = `not_picked_up` |  |
| `call.failed` | Q8 | count | `reason_code` |  |
| `queue.claimed` | Q7 | seconds in buckets | `dispatch_kind`, reason_code = `claimed` | The fact has its seconds: a call whose start or end was never seen has none |
| `queue.released` | Q7 | count | `dispatch_kind`, reason_code = `released` |  |
| `slot.closed` | C1 | The bookings of a closed slot that count (metric C1) | reason_code = `booked` |  |
| `slot.closed` | C1 | The capacity snapshot of a closed slot (metric C1) | reason_code = `capacity` |  |
| `slot.closed` | C2 | count | reason_code = Return how full a closed slot was: empty, under half, under full or full (metric C2). |  |
| `slot.closed` | C3 | The places of a closed slot nobody booked (metric C3) | none |  |
| `slot.shortfall` | C5 | count | `reason_code` |  |
| `message.sent` | M1 | count | `channel_kind`, reason_code = a state word |  |
| `message.failed` | M1 | count | `channel_kind`, reason_code = a state word |  |
| `trial.created` | T1 | count | reason_code = `created` |  |
| `trial.joined` | T1 | count | reason_code = `joined` |  |
| `trial.entered` | T1 | count | reason_code = `entered` |  |
| `trial.failed` | T1 | count | reason_code = `failed` |  |

## Closed lists

A `reason_code` of a fact is always one of these; a free text never becomes one.

- Failure codes (`call.failed` is `task.`, `meeting.` or `integration.` plus one of): `meeting_missing`, `other`, `rate_limited`, `rejected`, `server_error`, `unauthorized`, `unexpected_response`, `unreachable`
- Why a participant left a direct call: `agent`, `waiting`
- Purpose of a message: `cancellation`, `change`, `closure`, `confirm_request`, `confirmation`, `rebook`, `reminder`
- Shortfall of a slot: `open`, `accepted`, `resolved`
- Fill of a closed slot: `empty`, `under_half`, `under_full`, `full`

## How an outcome is derived

Nothing in the application sets a booking's status to `completed` or `no_show`, so the outcome is
derived from the call once the slot has ended and the late reports have arrived (two hours):

| What the call showed | Outcome |
|---|---|
| no meeting was made, or it failed | none (nothing was observed) |
| scheduled; the participant was seen and the host joined | `completed` |
| scheduled; the participant was seen, the host never joined | none (they came: see not picked up) |
| scheduled; the participant was not seen | `no_show` |
| direct; the participant was seen and the host joined | `completed` |
| direct; anything else | none |

The participant is *seen* when a guest-joined event or a poll showed someone besides the host in
the meeting, or two people were in it at once. `no_show` therefore means that no join was observed:
where Webex's webhooks are not set up and nobody opened the agenda, a join cannot be seen. A slot
exists only when it had a booking, so hours nobody booked are not in utilisation. Slots that ended
before the upgrade are not closed afterwards, and `completed` and `no_show` cannot be backfilled.

## Duration buckets

The same ten buckets for every duration; a signed duration (a host who was early) uses the mirrored set.

0. 0-1 min
1. 1-5 min
2. 5-15 min
3. 15-30 min
4. 30-60 min
5. 1-2 h
6. 2-6 h
7. 6-24 h
8. 1-7 d
9. over 7 d

## What a fact may hold

Exactly these fields and no others; `staff_id` only on the separate team-member fact.

- `kind`
- `bucket_hour`
- `organisation_id`
- `group_id`
- `booking_kind`
- `dispatch_kind`
- `subject_code`
- `locale`
- `channel_kind`
- `outcome`
- `actor_class`
- `reason_code`
- `value_seconds`
- `participants`
- `capacity`
- `booked`
- `staff_id`

## CSV export

Both files are UTF-8 with a byte order mark, `;` as separator, RFC 4180 quoting and CRLF line
ends; a text cell that starts like a formula gets a leading `'`. The response carries
`X-Content-SHA256` (the checksum of the file) and the audit event `reports.export` keeps it, with
the family, the range, the row count and who; never a figure. An export over
`reporting_export_max_rows` rows or `reporting_export_max_days` days is refused, never cut short.
Holders of `reports.export` download from the Usage page, for the groups of their scope.

**Screen CSV** (`?view=volume`, `queue`, `capacity` or `channels`): exactly the tables of that
page, after the small-cell rule, in the screen's order. Columns: `table`, `row`, `column`, `value`, `note`. `table` is the name of
the table, `row` and `column` are codes (a day, an outcome, a bucket number, a weekday 0 to 6 with
Monday first, an hour), `value` is the count, percent or seconds, `note` is `hidden` where the
screen shows `< n` or no figure, and the value is then empty. A total has the row or column
`total`. The file holds no interface text, so it does not change with the language.

**Tidy CSV `openplanner.usage/1`** (`?view=tidy`): one row per hourly cell, for analysts. Columns:
`hour`, `organisation`, `group`, `metric`, `booking_kind`, `dispatch_kind`, `subject`, `locale`, `channel`, `outcome`, `actor_class`, `reason`, `bucket`, `count`, `sum`, `max`. `hour` is the UTC hour (RFC 3339), `organisation` and `group` are slugs, `metric` is an ID
of the table above, the cut columns hold the codes of the cell (empty when the metric is not cut
by it), `bucket` is the duration bucket number, `count` is the number of facts, `sum` is what the
metric adds up and `max` is the largest value (Q9's peak). A cell below the threshold is not in the
file at all. The range is whole group-local days. The column list and its meaning change only with
a new version, which needs an amendment of the ADR.
