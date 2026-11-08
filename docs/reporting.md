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
| Q4 | Participant wait in the meeting | `host_joined_at - max(slot.start, guest_joined_at)` when positive. | `call.host_joined` | sum |
| Q5 | Call length | `ended_at - started_at` where both are set; buckets, exact mean. | `call.ended` | sum |
| Q6 | Abandoned direct calls | `waiting_left / direct calls created`, with the wait at the moment of leaving in buckets; and the count not picked up within `not_picked_up_minutes`. | `call.waiting_left`, `call.not_picked_up` | count |
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
| `call.ended` | event |
| `call.waiting_left` | event |
| `call.not_picked_up` | event |
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
