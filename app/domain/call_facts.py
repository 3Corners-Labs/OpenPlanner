"""The usage facts of a booking's call and trial call (ADR 0117 step 3; pure functions).

A booking's timeline gets an event whenever Webex or WxCC reports something, a participant
leaves a direct call or a job records a failure. ``call_facts`` turns one such event, together
with the call status before and after it, into the facts the metrics of ADR 0117 need:

    task_created, fallback_queued     ─► ``call.created``                          (Q6, Q9)
    task_connected                    ─► ``call.connected``, task wait             (Q2)
    host_joined                       ─► ``call.host_joined``, signed punctuality  (Q3)
                                         ``call.guest_waited``, the guest's wait   (Q4)
    meeting_ended                     ─► ``call.ended``, length, most people seen  (Q5, U11)
    waiting_left                      ─► ``call.waiting_left``, wait when leaving  (Q6)
    task_failed, meeting_failed,
    integration_error                 ─► ``call.failed`` with a code of a closed list (Q8)
    trial_created / joined / entered  ─► ``trial.created`` / ``joined`` / ``entered`` (once each)
    trial_failed                      ─► ``trial.failed``                           (T1)

A fact follows a *change* of the call status, never an event as such, so a repeated event writes
nothing twice (webhooks are deduplicated by their key; a poll reports a transition once). The
durations are exact seconds: unlike a lead time they are not in a booking row, so they cannot
be matched to one (ADR 0117 step 2 notes). Nothing here reads a name, an address, a link, a
booking id or a provider's text: a failure is reduced to a code of ``FAILURE_CODES``.
"""

from datetime import datetime

from app.domain.booking import BookingKind
from app.domain.booking_facts import BookingFacts, keyed
from app.domain.calls import CallState, CallStatus, EventKind, TaskState
from app.domain.direct_leave import DETAIL_AGENT, DETAIL_WAITING
from app.domain.usage import ActorClass, FactKind, UsageFact

FAILURE_CODES: frozenset[str] = frozenset(
    {
        "unauthorized",
        "rate_limited",
        "server_error",
        "rejected",
        "unreachable",
        "unexpected_response",
        "meeting_missing",
        "other",
    }
)
"""The closed list a failure is reduced to (metric Q8). ``rejected_400`` and the like are one
``rejected``: the status number says little that the group check does not, and a free ``detail``
(a webhook's text) can carry anything."""

_FAILURES: dict[EventKind, str] = {
    EventKind.TASK_FAILED: "task",
    EventKind.MEETING_FAILED: "meeting",
    EventKind.INTEGRATION_ERROR: "integration",
}
"""The part of the platform that failed, the first half of a failure's ``reason_code``."""

LEAVE_REASONS: frozenset[str] = frozenset({DETAIL_WAITING, DETAIL_AGENT})
"""Why a participant stopped waiting for a direct call: before an agent came, or once one had."""

FIRST_ONLY: frozenset[EventKind] = frozenset(
    {EventKind.TRIAL_CREATED, EventKind.TRIAL_JOINED, EventKind.TRIAL_ENTERED}
)
"""Trial events that count once per booking (metric T1 is a share of bookings): the caller says
whether this is the first of its kind in the booking's timeline."""

PRODUCES: frozenset[EventKind] = frozenset(
    {
        EventKind.TASK_CREATED,
        EventKind.FALLBACK_QUEUED,
        EventKind.TASK_CONNECTED,
        EventKind.HOST_JOINED,
        EventKind.MEETING_ENDED,
        EventKind.WAITING_LEFT,
        EventKind.TRIAL_FAILED,
        *_FAILURES,
        *FIRST_ONLY,
    }
)
"""The events that can make a fact; the others are skipped before any booking is loaded."""

_TRIAL: dict[EventKind, FactKind] = {
    EventKind.TRIAL_CREATED: FactKind.TRIAL_CREATED,
    EventKind.TRIAL_JOINED: FactKind.TRIAL_JOINED,
    EventKind.TRIAL_ENTERED: FactKind.TRIAL_ENTERED,
    EventKind.TRIAL_FAILED: FactKind.TRIAL_FAILED,
}

_HOST = "host"
"""Detail of a trial entry by a host, which nobody from the team should do: not a guest."""


def failure_code(detail: str) -> str:
    """Return the code of ``FAILURE_CODES`` that a timeline ``detail`` stands for.

    ``rejected_400`` is ``rejected``; the poll's ``polled:`` prefix is dropped; anything else
    that is not on the list is ``other``, so a provider's text never becomes a code.
    """
    code = detail.rsplit(":", 1)[-1].strip().lower()
    if code.startswith("rejected_"):
        return "rejected"
    return code if code in FAILURE_CODES else "other"


def needs_load(kind: EventKind, b: BookingFacts) -> bool:
    """Return whether the fact of ``kind`` carries the direct-call load (metric Q9)."""
    return kind in (EventKind.TASK_CREATED, EventKind.FALLBACK_QUEUED) and (
        b.booking_kind is BookingKind.DIRECT
    )


def _seconds(later: datetime | None, earlier: datetime | None) -> int | None:
    """Return the whole seconds from ``earlier`` to ``later``, or ``None`` if one is unknown."""
    if later is None or earlier is None:
        return None
    return int((later - earlier).total_seconds())


def call_facts(
    kind: EventKind,
    b: BookingFacts,
    before: CallStatus,
    after: CallStatus,
    at: datetime,
    detail: str = "",
    *,
    load: int | None = None,
    first_of_kind: bool = True,
) -> list[UsageFact]:
    """Return the facts that ``kind`` at ``at`` adds, given the status before and after it.

    Args:
        kind: The timeline event.
        b: The booking, as the fact builders describe it (no personal data).
        before: The call status before the event.
        after: The call status after it (``calls.apply_event`` or ``apply_polled``).
        at: When it happened.
        detail: The event's reason code; reduced to a closed list before it is used.
        load: For a direct call's creation, the direct calls waiting or connected in the group
            right after it (see ``needs_load``).
        first_of_kind: For ``FIRST_ONLY`` events, whether the booking's timeline held none of
            its kind before; the caller reads it.
    """
    if kind in (EventKind.TASK_CREATED, EventKind.FALLBACK_QUEUED):
        return _created(kind, b, before, at, load)
    if kind is EventKind.TASK_CONNECTED:
        return _connected(b, before, after, at)
    if kind is EventKind.HOST_JOINED:
        return _host_joined(b, before, after, at)
    if kind is EventKind.MEETING_ENDED:
        return _ended(b, before, after, at)
    if kind is EventKind.WAITING_LEFT:
        reason = detail if detail in LEAVE_REASONS else None
        wait = _seconds(at, b.start)
        return [keyed(FactKind.CALL_WAITING_LEFT, b, at, reason_code=reason, value_seconds=wait)]
    if kind in _FAILURES:
        return _failed(kind, b, before, at, detail)
    if kind in FIRST_ONLY and not first_of_kind:
        return []
    if kind is EventKind.TRIAL_ENTERED and detail == _HOST:
        return []
    if kind in _TRIAL:
        return [keyed(_TRIAL[kind], b, at)]
    return []


def _created(
    kind: EventKind, b: BookingFacts, before: CallStatus, at: datetime, load: int | None
) -> list[UsageFact]:
    """The work item was created, or the booking went to the own queue instead."""
    if before.task_created_at is not None:
        return []  # the work item exists already: a second task event is no new call
    if kind is EventKind.FALLBACK_QUEUED:
        if before.fallback:
            return []
        return [keyed(FactKind.CALL_CREATED, b, at, reason_code="fallback", participants=load)]
    return [keyed(FactKind.CALL_CREATED, b, at, participants=load)]


def _connected(
    b: BookingFacts, before: CallStatus, after: CallStatus, at: datetime
) -> list[UsageFact]:
    """An agent accepted the work item; the value is the wait since it was created (Q2)."""
    if before.connected_at is not None or after.connected_at is None:
        return []
    wait = _seconds(after.connected_at, after.task_created_at)
    return [keyed(FactKind.CALL_CONNECTED, b, at, value_seconds=wait)]


def _host_joined(
    b: BookingFacts, before: CallStatus, after: CallStatus, at: datetime
) -> list[UsageFact]:
    """The host entered the meeting: punctuality (Q3) and, if a guest waited, their wait (Q4)."""
    if before.host_joined or after.host_joined_at is None:
        return []
    scheduled = b.booking_kind is BookingKind.SCHEDULED
    facts = [
        keyed(
            FactKind.CALL_HOST_JOINED,
            b,
            at,
            value_seconds=_seconds(after.host_joined_at, b.start) if scheduled else None,
        )
    ]
    if scheduled and after.guest_joined_at is not None:
        waited = _seconds(after.host_joined_at, max(b.start, after.guest_joined_at))
        if waited is not None and waited > 0:
            facts.append(keyed(FactKind.CALL_GUEST_WAITED, b, at, value_seconds=waited))
    return facts


def _ended(b: BookingFacts, before: CallStatus, after: CallStatus, at: datetime) -> list[UsageFact]:
    """The meeting ended: its length (Q5) and the most people seen in it (U11)."""
    if before.call is CallState.ENDED:
        return []
    length = _seconds(after.ended_at, after.started_at)
    return [
        keyed(
            FactKind.CALL_ENDED,
            b,
            at,
            value_seconds=length if length is not None and length >= 0 else None,
            participants=after.peak_participants,
        )
    ]


def _failed(
    kind: EventKind, b: BookingFacts, before: CallStatus, at: datetime, detail: str
) -> list[UsageFact]:
    """A platform call failed; the fact carries the part and a code of the closed list (Q8)."""
    repeat = (kind is EventKind.TASK_FAILED and before.task is TaskState.FAILED) or (
        kind is EventKind.MEETING_FAILED and before.call is CallState.FAILED
    )
    if repeat:
        return []
    code = f"{_FAILURES[kind]}.{failure_code(detail)}"
    return [keyed(FactKind.CALL_FAILED, b, at, actor_class=ActorClass.SYSTEM, reason_code=code)]
