"""Facts, duration buckets and the metric catalogue of reporting on use (ADR 0117 step 1).

Reporting is fed when things happen, not when someone asks, because the source rows go with the
purge (ADR 0102). A *fact* is one small record of a state change: an hour, an organisation and
a group, a kind, and a few codes and numbers from a fixed allow-list (``privacy.USAGE_FIELDS``).
It holds no exact time, no booking id, no reference and no free text, so it cannot be joined
back to an appointment (abuse case R3). The types check themselves, so a fact that was built
directly, or read back from storage, passes the same rules as one built by a service.

    state change ─► UsageFact(bucket_hour=hour_of(at), ...) ─► usage_events    (step 2)
                 ─► rollup per hour and cut ─► ``UsageCell`` ─► usage_hour     (step 4)
                 ─► every read: ``usage_suppress`` (small cells)               (step 5)

This module holds what screens, exports and the API share: the fact kinds and the instant each
is keyed on, the fixed duration buckets and the metric catalogue (``METRICS``), from which
``docs/reporting.md`` is generated. The small-cell rules are in ``usage_suppress``.

No database, no framework: the storage is step 2, the rollup step 4.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind

METRIC_VERSION = 1
"""Version of the metric definitions; a change of a definition or a bucket bound needs a new one."""

_CODE = re.compile(r"^(?=.*[a-z])[a-z0-9][a-z0-9_.-]{0,39}$")
_LOCALE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


class FactKind(StrEnum):
    """What happened; the *Fact(s)* column of the metric catalogue (ADR 0117 point 6)."""

    BOOKING_CREATED = "booking.created"
    BOOKING_SLOTTED = "booking.slotted"
    BOOKING_CONFIRMED = "booking.confirmed"
    BOOKING_CONFIRM_EXPIRED = "booking.confirm_expired"
    BOOKING_CANCELLED = "booking.cancelled"
    BOOKING_MOVED = "booking.moved"
    BOOKING_OUTCOME = "booking.outcome"
    BOOKING_SUSPENDED = "booking.suspended"
    BOOKING_RESUMED = "booking.resumed"
    REBOOK_PROPOSED = "rebook.proposed"
    REBOOK_ACCEPTED = "rebook.accepted"
    REBOOK_EXPIRED = "rebook.expired"
    CALL_CREATED = "call.created"
    CALL_CONNECTED = "call.connected"
    CALL_HOST_JOINED = "call.host_joined"
    CALL_ENDED = "call.ended"
    CALL_WAITING_LEFT = "call.waiting_left"
    CALL_NOT_PICKED_UP = "call.not_picked_up"
    CALL_FAILED = "call.failed"
    QUEUE_CLAIMED = "queue.claimed"
    QUEUE_RELEASED = "queue.released"
    SLOT_CLOSED = "slot.closed"
    SLOT_SHORTFALL = "slot.shortfall"
    MESSAGE_SENT = "message.sent"
    MESSAGE_FAILED = "message.failed"
    TRIAL_CREATED = "trial.created"
    TRIAL_JOINED = "trial.joined"
    TRIAL_ENTERED = "trial.entered"
    TRIAL_FAILED = "trial.failed"


class HourSource(StrEnum):
    """The instant a fact's hour is taken from (ADR 0117 point 3), declared once per kind."""

    CREATION = "creation"
    """When the record was made: the creation hour of a booking."""
    SLOT_START = "slot_start"
    """The start of the slot: the cohort of point 6, so no query holds its own cohort rule."""
    EVENT = "event"
    """The moment the event itself happened."""


FACT_HOUR: dict[FactKind, HourSource] = {
    FactKind.BOOKING_CREATED: HourSource.CREATION,
    FactKind.BOOKING_SLOTTED: HourSource.SLOT_START,
    FactKind.BOOKING_CONFIRMED: HourSource.EVENT,
    FactKind.BOOKING_CONFIRM_EXPIRED: HourSource.SLOT_START,
    FactKind.BOOKING_CANCELLED: HourSource.SLOT_START,
    FactKind.BOOKING_MOVED: HourSource.EVENT,
    FactKind.BOOKING_OUTCOME: HourSource.SLOT_START,
    FactKind.BOOKING_SUSPENDED: HourSource.EVENT,
    FactKind.BOOKING_RESUMED: HourSource.EVENT,
    FactKind.REBOOK_PROPOSED: HourSource.EVENT,
    FactKind.REBOOK_ACCEPTED: HourSource.EVENT,
    FactKind.REBOOK_EXPIRED: HourSource.EVENT,
    FactKind.CALL_CREATED: HourSource.EVENT,
    FactKind.CALL_CONNECTED: HourSource.EVENT,
    FactKind.CALL_HOST_JOINED: HourSource.EVENT,
    FactKind.CALL_ENDED: HourSource.EVENT,
    FactKind.CALL_WAITING_LEFT: HourSource.EVENT,
    FactKind.CALL_NOT_PICKED_UP: HourSource.EVENT,
    FactKind.CALL_FAILED: HourSource.EVENT,
    FactKind.QUEUE_CLAIMED: HourSource.EVENT,
    FactKind.QUEUE_RELEASED: HourSource.EVENT,
    FactKind.SLOT_CLOSED: HourSource.SLOT_START,
    FactKind.SLOT_SHORTFALL: HourSource.SLOT_START,
    FactKind.MESSAGE_SENT: HourSource.EVENT,
    FactKind.MESSAGE_FAILED: HourSource.EVENT,
    FactKind.TRIAL_CREATED: HourSource.EVENT,
    FactKind.TRIAL_JOINED: HourSource.EVENT,
    FactKind.TRIAL_ENTERED: HourSource.EVENT,
    FactKind.TRIAL_FAILED: HourSource.EVENT,
}
"""The instant each kind is keyed on. ``booking.slotted`` is one fact per booking placed in a
slot, keyed on the slot start, so the busiest-hours table (C4) needs no second hour on
``booking.created``."""


class ActorClass(StrEnum):
    """Who caused a change, as a class and never as a person (ADR 0117 point 2)."""

    PARTICIPANT = "participant"
    PLANNER = "planner"
    CLOSURE = "closure"
    SYSTEM = "system"


class UsageOutcome(StrEnum):
    """How a booking ended, for the outcome counts (metric U2)."""

    COMPLETED = "completed"
    NO_SHOW = "no_show"
    CANCELLED_BY_PARTICIPANT = "cancelled_by_participant"
    CANCELLED_BY_PLANNER = "cancelled_by_planner"
    CANCELLED_BY_CLOSURE = "cancelled_by_closure"
    CANCELLED_BY_SYSTEM = "cancelled_by_system"
    EXPIRED_UNCONFIRMED = "expired_unconfirmed"


_CANCELLED_BY = {
    ActorClass.PARTICIPANT: UsageOutcome.CANCELLED_BY_PARTICIPANT,
    ActorClass.PLANNER: UsageOutcome.CANCELLED_BY_PLANNER,
    ActorClass.CLOSURE: UsageOutcome.CANCELLED_BY_CLOSURE,
    ActorClass.SYSTEM: UsageOutcome.CANCELLED_BY_SYSTEM,
}


def cancelled_outcome(actor: ActorClass) -> UsageOutcome:
    """Return the outcome of a booking cancelled by ``actor`` (metric U2)."""
    return _CANCELLED_BY[actor]


class Aggregation(StrEnum):
    """How a metric's facts add up in one hour: a count, a sum, or the largest value."""

    COUNT = "count"
    SUM = "sum"
    MAX = "max"


@dataclass(frozen=True)
class UsageFact:
    """One state change, with the allow-listed fields and nothing else (ADR 0117 point 2).

    Every field is in ``privacy.USAGE_FIELDS``; a test fails when one is added without being
    listed there, or when one is a personal-data field.

    Attributes:
        kind: What happened.
        bucket_hour: The UTC hour of the instant the kind is keyed on (``FACT_HOUR``), made
            with ``hour_of``; a time with minutes is refused.
        organisation_id: The organisation.
        group_id: The group.
        booking_kind: Scheduled or direct.
        dispatch_kind: How the booking reaches staff.
        subject_code: A subject code of the closed list (ADR 0081), empty until built; the
            fact checks the shape, the caller passes a member of the list.
        locale: The participant's language tag, such as ``nl``.
        channel_kind: Email or SMS, for message facts.
        outcome: How a booking ended.
        actor_class: Who caused the change, as a class.
        reason_code: A code from a closed list; ``move`` marks a booking created by a move.
        value_seconds: A duration in seconds; signed for a host that was early (Q3).
        participants: How many people took part in a call.
        capacity: The capacity snapshot of a slot.
        booked: The bookings of a slot that count (confirmed, completed, no show).
    """

    kind: FactKind
    bucket_hour: datetime
    organisation_id: int
    group_id: int
    booking_kind: BookingKind | None = None
    dispatch_kind: DispatchKind | None = None
    subject_code: str | None = None
    locale: str | None = None
    channel_kind: ChannelKind | None = None
    outcome: UsageOutcome | None = None
    actor_class: ActorClass | None = None
    reason_code: str | None = None
    value_seconds: int | None = None
    participants: int | None = None
    capacity: int | None = None
    booked: int | None = None

    def __post_init__(self) -> None:
        """Refuse anything the allow-list rules out; every way of building a fact comes here.

        Raises:
            ValueError: If ``bucket_hour`` is not a UTC hour (use ``hour_of``), a code is not a
                code, the locale is not a short language tag, or a count is negative.
        """
        _hour(self.bucket_hour)
        _code("subject_code", self.subject_code)
        _code("reason_code", self.reason_code)
        if self.locale is not None and not _LOCALE.match(self.locale):
            raise ValueError("locale is not a language tag")
        _count("participants", self.participants)
        _count("capacity", self.capacity)
        _count("booked", self.booked)


@dataclass(frozen=True)
class StaffFact:
    """A fact about one team member (ADR 0117 point 9); the only shape that holds ``staff_id``.

    Written only while an organisation has switched the team view on, and folded into one
    *former team members* row when the team member is removed.

    Attributes:
        kind: What happened.
        bucket_hour: The UTC hour of the event.
        organisation_id: The organisation.
        group_id: The group.
        staff_id: The row id of the ``staff`` entry; never the agent pseudonym of a booking.
        value_seconds: A duration in seconds, for call length.
    """

    kind: FactKind
    bucket_hour: datetime
    organisation_id: int
    group_id: int
    staff_id: int
    value_seconds: int | None = None

    def __post_init__(self) -> None:
        """Refuse a moment that is not a UTC hour and a ``staff_id`` that is no row id."""
        _hour(self.bucket_hour)
        if self.staff_id < 1:
            raise ValueError("staff_id is not a row id")


def hour_of(moment: datetime) -> datetime:
    """Return the UTC hour that contains ``moment``, without minutes, seconds or microseconds.

    Raises:
        ValueError: If ``moment`` has no timezone; a time without one cannot be placed.
    """
    if moment.tzinfo is None:
        raise ValueError("a moment needs a timezone")
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def usable_locale(text: str | None) -> str | None:
    """Return ``text`` when it is a short language tag a fact accepts, else ``None``.

    A booking's locale comes from the participant's browser; a fact never refuses the
    participant's action over it, so a value that is no tag is left out of the fact.
    """
    return text if text is not None and _LOCALE.match(text) else None


def _hour(moment: datetime) -> None:
    """Refuse a ``bucket_hour`` that is not exactly a UTC hour: no minutes, no exact time."""
    if moment.tzinfo is None or moment != hour_of(moment) or moment.utcoffset() != timedelta(0):
        raise ValueError("bucket_hour is not a UTC hour")


def _code(name: str, value: str | None) -> None:
    """Refuse ``value`` unless it is a short code with a letter in it.

    A shape check only: the caller passes a member of a closed list (ADR 0081 subject codes, a
    reason enum). A digits-only string, which could be a phone number, is refused here too.
    """
    if value is not None and not _CODE.match(value):
        raise ValueError(f"{name} is not a code")


def _count(name: str, value: int | None) -> None:
    """Refuse a negative count."""
    if value is not None and value < 0:
        raise ValueError(f"{name} is negative")


@dataclass(frozen=True)
class UsageCell:
    """One row of the long-lived rollup: an hour and a cut, with a count and two figures.

    No JSON or string key crosses the adapter boundary: every cut is a typed nullable column
    (ADR 0117 point 3). The cut columns are the same codes a fact carries.

    Attributes:
        bucket_hour: The UTC hour.
        organisation_id: The organisation.
        group_id: The group.
        metric: The metric id of ``METRICS``, such as ``U1``.
        count: How many facts fell in this hour and cut.
        sum_value: The exact sum of ``value_seconds`` (or the metric's own number).
        max_value: The largest value, which metric Q9 needs.
        booking_kind: Cut by scheduled or direct.
        dispatch_kind: Cut by queue method.
        subject_code: Cut by subject.
        locale: Cut by language.
        channel_kind: Cut by channel.
        outcome: Cut by outcome.
        actor_class: Cut by who caused it.
        reason_code: Cut by reason.
        bucket: The duration bucket code (``duration_bucket``), when the metric is a duration.
    """

    bucket_hour: datetime
    organisation_id: int
    group_id: int
    metric: str
    count: int
    sum_value: int
    max_value: int
    booking_kind: BookingKind | None = None
    dispatch_kind: DispatchKind | None = None
    subject_code: str | None = None
    locale: str | None = None
    channel_kind: ChannelKind | None = None
    outcome: UsageOutcome | None = None
    actor_class: ActorClass | None = None
    reason_code: str | None = None
    bucket: int | None = None


# Fixed duration buckets (ADR 0117 point 7): constants on purpose, because changing them would
# make history incomparable. A change needs a new ``METRIC_VERSION``.
BUCKET_BOUNDS_SECONDS: tuple[int, ...] = (60, 300, 900, 1800, 3600, 7200, 21600, 86400, 604800)
"""Upper bounds (exclusive) of the first nine buckets; the tenth is everything beyond."""

BUCKET_LABELS: tuple[str, ...] = (
    "0-1 min",
    "1-5 min",
    "5-15 min",
    "15-30 min",
    "30-60 min",
    "1-2 h",
    "2-6 h",
    "6-24 h",
    "1-7 d",
    "over 7 d",
)
"""One label per bucket, in order; the same set for every duration."""


def duration_bucket(seconds: int, *, signed: bool = False) -> int:
    """Return the bucket code of a duration.

    Unsigned codes run from 0 (under a minute) to 9 (over seven days). A signed duration
    (a host who joined early, Q3) uses the mirrored set: a negative value ``-s`` has code
    ``-(bucket(s) + 1)``, so -1 to -10, and zero stays on the positive side.

    Raises:
        ValueError: If ``seconds`` is negative and ``signed`` is not set.
    """
    if seconds < 0:
        if not signed:
            raise ValueError("a negative duration needs the signed buckets")
        return -(duration_bucket(-seconds) + 1)
    return bisect_right(BUCKET_BOUNDS_SECONDS, seconds)


def bucket_label(code: int) -> str:
    """Return the label of a bucket code; a mirrored one reads ``-(1-5 min)``.

    Raises:
        ValueError: If ``code`` is outside -10 to 9.
    """
    if 0 <= code < len(BUCKET_LABELS):
        return BUCKET_LABELS[code]
    if -len(BUCKET_LABELS) <= code < 0:
        return f"-({BUCKET_LABELS[-code - 1]})"
    raise ValueError("bucket code out of range")


@dataclass(frozen=True)
class Metric:
    """One metric of the catalogue: the contract of the screens, the CSV and the API.

    Attributes:
        id: The stable id, such as ``U1``; never reused.
        name: A short English name; the interface uses the i18n key ``reports.metric.<id>``.
        definition: The exact definition, as ADR 0117 point 6 states it.
        facts: The fact kinds the metric is made from.
        aggregation: How the facts of one hour add up.
        staff: Whether it is about a team member (switched off by default, point 9).
    """

    id: str
    name: str
    definition: str
    facts: tuple[FactKind, ...]
    aggregation: Aggregation
    staff: bool = False


_F = FactKind
_COUNT, _SUM, _MAX = Aggregation.COUNT, Aggregation.SUM, Aggregation.MAX

METRICS: tuple[Metric, ...] = (
    # Volume and outcome
    Metric(
        "U1",
        "Bookings created",
        "Count of `booking.created` that are no move, by creation hour.",
        (_F.BOOKING_CREATED,),
        _COUNT,
    ),
    Metric(
        "U2",
        "Outcomes",
        "Count per final outcome: completed, no show, cancelled by participant, by planner, "
        "by closure, by system, expired unconfirmed.",
        (_F.BOOKING_OUTCOME, _F.BOOKING_CANCELLED, _F.BOOKING_CONFIRM_EXPIRED),
        _COUNT,
    ),
    Metric(
        "U3",
        "No-show rate",
        "`no_show / (completed + no_show)` in the cohort; cancelled and suspended excluded.",
        (_F.BOOKING_OUTCOME,),
        _COUNT,
    ),
    Metric(
        "U4",
        "Cancellation rate",
        "Cancelled by participant over bookings created in the cohort, excluding moves.",
        (_F.BOOKING_CANCELLED, _F.BOOKING_CREATED),
        _COUNT,
    ),
    Metric(
        "U5",
        "Late cancellation",
        "Share of cancellations whose lead time is inside the group's "
        "`participant_change_lead_minutes` (ADR 0069).",
        (_F.BOOKING_CANCELLED,),
        _COUNT,
    ),
    Metric(
        "U6",
        "Lead time",
        "`slot.start - created_at`, in the duration buckets, per created booking.",
        (_F.BOOKING_CREATED,),
        _SUM,
    ),
    Metric(
        "U7",
        "Reschedules",
        "Count of `booking.moved` by actor class and reason; rate `moved / bookings in the "
        "cohort`.",
        (_F.BOOKING_MOVED,),
        _COUNT,
    ),
    Metric(
        "U8",
        "Rebooking funnel",
        "Proposed, accepted, expired; conversion `accepted / proposed`; time to accept in "
        "buckets; split by planner-asked and assistant-proposed (ADR 0044, 0057).",
        (_F.REBOOK_PROPOSED, _F.REBOOK_ACCEPTED, _F.REBOOK_EXPIRED),
        _COUNT,
    ),
    Metric(
        "U9",
        "Confirmation conversion",
        "`confirmed / (confirmed + expired unconfirmed)`; time to confirm in buckets (ADR 0056).",
        (_F.BOOKING_CONFIRMED, _F.BOOKING_CONFIRM_EXPIRED),
        _COUNT,
    ),
    Metric(
        "U10",
        "Closure impact",
        "Bookings suspended, resumed, cancelled per closure; no closure id is stored, only the "
        "day and counts (ADR 0024).",
        (_F.BOOKING_SUSPENDED, _F.BOOKING_RESUMED),
        _COUNT,
    ),
    Metric(
        "U11",
        "Participants per call",
        "Sum and mean of `participants` of finished calls.",
        (_F.CALL_ENDED,),
        _SUM,
    ),
    # Queue method and time
    Metric(
        "Q1",
        "Queue-method mix",
        "Share of bookings by dispatch kind (contact center, own queue, calendar invite) and "
        "booking kind (scheduled, direct, including start now; ADR 0019, 0072, 0043).",
        (_F.BOOKING_CREATED,),
        _COUNT,
    ),
    Metric(
        "Q2",
        "Task wait",
        "`connected_at - task_created_at` of a call dispatched to the contact center or the "
        "own queue; buckets, exact mean.",
        (_F.CALL_CONNECTED,),
        _SUM,
    ),
    Metric(
        "Q3",
        "Host punctuality",
        "`host_joined_at - slot.start`, signed (negative: the host was early); only scheduled "
        "calls.",
        (_F.CALL_HOST_JOINED,),
        _SUM,
    ),
    Metric(
        "Q4",
        "Participant wait in the meeting",
        "`host_joined_at - max(slot.start, guest_joined_at)` when positive.",
        (_F.CALL_HOST_JOINED,),
        _SUM,
    ),
    Metric(
        "Q5",
        "Call length",
        "`ended_at - started_at` where both are set; buckets, exact mean.",
        (_F.CALL_ENDED,),
        _SUM,
    ),
    Metric(
        "Q6",
        "Abandoned direct calls",
        "`waiting_left / direct calls created`, with the wait at the moment of leaving in "
        "buckets; and the count not picked up within `not_picked_up_minutes`.",
        (_F.CALL_WAITING_LEFT, _F.CALL_NOT_PICKED_UP),
        _COUNT,
    ),
    Metric(
        "Q7",
        "Own-queue claim",
        "Share claimed, time from the queue becoming visible to `claimed`, releases per claim.",
        (_F.QUEUE_CLAIMED, _F.QUEUE_RELEASED),
        _COUNT,
    ),
    Metric(
        "Q8",
        "Dispatch health",
        "Count of `task_failed`, `meeting_failed` and `integration_error` per day, by a code "
        "from a closed list (not the provider's text).",
        (_F.CALL_FAILED,),
        _COUNT,
    ),
    Metric(
        "Q9",
        "Direct-call load",
        "Peak concurrent waiting-plus-connected per group and hour (ADR 0072 cap), computed "
        "from facts, not from live state.",
        (_F.CALL_CREATED, _F.CALL_CONNECTED, _F.CALL_ENDED),
        _MAX,
    ),
    # Capacity and load
    Metric(
        "C1",
        "Utilisation",
        "`sum(booked) / sum(capacity_snapshot)` over slots that have started; `booked` is "
        "confirmed plus completed plus no show, not cancelled and not pending.",
        (_F.SLOT_CLOSED,),
        _SUM,
    ),
    Metric(
        "C2",
        "Fill distribution",
        "Slots by fill: empty, under half, under full, full.",
        (_F.SLOT_CLOSED,),
        _COUNT,
    ),
    Metric(
        "C3",
        "Idle capacity",
        "`sum(capacity - booked)` over started slots.",
        (_F.SLOT_CLOSED,),
        _SUM,
    ),
    Metric(
        "C4",
        "Busiest hours and days",
        "Bookings by group-local weekday and hour of the slot start, as a table with a heat "
        "shade and the number in each cell; derived at render time.",
        (_F.BOOKING_SLOTTED,),
        _COUNT,
    ),
    Metric(
        "C5",
        "Shortfall",
        "Blocks where staffed agents were fewer than bookings (ADR 0044), split accepted and "
        "resolved.",
        (_F.SLOT_SHORTFALL,),
        _COUNT,
    ),
    Metric(
        "S1",
        "Load per team member",
        "Calls handled, call minutes, claims, on-time share per `staff_id` (ADR 0117 point 9).",
        (_F.CALL_ENDED, _F.QUEUE_CLAIMED),
        _SUM,
        staff=True,
    ),
    Metric(
        "S2",
        "Team utilisation",
        "`handled call seconds / staffed seconds` of the blocks in the group's staffing plan.",
        (_F.CALL_ENDED, _F.SLOT_CLOSED),
        _SUM,
        staff=True,
    ),
    # Channels, locales, trial calls
    Metric(
        "M1",
        "Messages",
        "Sent and failed per channel kind (email, sms) and purpose (confirmation, reminder, "
        "change, cancellation); failure rate.",
        (_F.MESSAGE_SENT, _F.MESSAGE_FAILED),
        _COUNT,
    ),
    Metric(
        "M2",
        "Locale mix",
        "Bookings by locale.",
        (_F.BOOKING_CREATED,),
        _COUNT,
    ),
    Metric(
        "T1",
        "Trial calls",
        "Share of bookings with a trial created; of those, followed the link; of those, "
        "entered the meeting (the gap is people stuck in the lobby); failures "
        "(ADR 0071, 0076).",
        (_F.TRIAL_CREATED, _F.TRIAL_JOINED, _F.TRIAL_ENTERED, _F.TRIAL_FAILED),
        _COUNT,
    ),
)
"""The metric catalogue of ADR 0117 point 6; ``docs/reporting.md`` is generated from it."""
