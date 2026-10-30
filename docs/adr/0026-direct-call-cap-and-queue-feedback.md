# 0026. Cap on open direct calls and queue feedback from our own data

Status: accepted (2026-10-26), requested by the product owner. Amends ADR 0019 (consequence
"capacity of direct calls is managed by WxCC"). Amended by ADR 0070 (a claimed call counts as
connected; the waiting page polls a fragment instead of waiting for the refresh button).

## Context

ADR 0019 lets citizens start a video call right away and leaves capacity to WxCC. In practice a
group has a few agents for direct calls; when more citizens start a call than there are agents,
the WxCC queue fills up with calls nobody takes, and every one of them ends as "no agent
available" after `not_picked_up_minutes`. Citizens also wait without knowing how long.

WxCC can report live queue statistics, but which API, fields and scope that takes for our
tenant is not confirmed yet. The product owner asked for the cap and for wait-time feedback
now, from what OpenPlanner already knows, and to add WxCC live statistics later.

## Decision

1. **Cap per group.** A group setting `direct_call_max_concurrent` (1 to 1000, empty = no cap,
   the default): how many direct calls may be open at once, typically the number of agents for
   direct calls. Open means **waiting** (task none or queued, no host yet, at most
   `not_picked_up_minutes` after the start) or **connected** (task connected or host joined,
   until the task or meeting ends, at most 30 minutes after the booking's end). Ended, failed,
   cancelled and stale calls do not count, so a lost `task:ended` webhook frees the place on its
   own. Scheduled calls are not counted: their load is planned through slot capacity.
2. **Checked in the booking transaction.** `start_direct` counts the open calls inside the same
   locked transaction (`BEGIN IMMEDIATE`) that inserts the booking, so two citizens cannot both
   take the last place. A full group refuses with `direct_call_full` (HTTP 409).
3. **Honest pages.** The group page and the form say "all agents are busy, try again in a few
   minutes or book an appointment" instead of offering the call; closed (switched off, outside
   opening hours, holiday, emergency closure) still wins over full.
4. **Queue feedback from our own data.** The form shows how many direct calls wait now; the
   waiting page shows the citizen's place in line among waiting direct calls (earlier start
   first) and the usual wait: the median time from task creation to `task:connected` of the
   group's latest 20 pickups in the past 7 days, rounded up to whole minutes, shown only with at
   least 3 pickups (and the upper middle one for an even count: rather too long than too short).
   The waiting page still never reloads by itself (WCAG 2.2.1); the refresh button updates it.
5. **One home.** The rules live in `app/domain/direct_queue.py` (pure); the service loads the
   calls through `Transaction.get_direct_calls(group_id, since)`, a projection without personal
   data.

## Consequences

- No new personal data, no new external calls and nothing new in WxCC payloads. The usual wait
  uses `call_status`, which has no personal data and is kept after the purge.
- The place in line counts only direct calls; scheduled tasks in the same WxCC queue and WxCC's
  own priorities are not visible to us, so the place and the usual wait are an indication, and
  the texts say "usually" and "about".
- Bots that pass the rate limit and honeypot can take the open places for at most
  `not_picked_up_minutes`; the rate limit (10 per IP per hour) bounds how many places one source
  holds (threat model).
- Later: WxCC live statistics (queue length, available agents, estimated wait) can feed the same
  `QueueInfo` and could make the cap follow the agents actually available. That needs a new ADR
  with the confirmed API and scope.
