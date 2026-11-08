"""A held proposal of a new time for a booking that has to move (ADR 0057 points 7–9).

The rebooking assistant (``rebooking``) picks a new time; this module is what is stored and
decided about it. The booking itself awaits a new time (ADR 0044); the proposal holds one place
in the proposed slot, counted like a pending confirmation (ADR 0056), until the participant
answers or the deadline passes:

    held ──confirm────────► released (confirmed): the booking moves into the proposed slot
         ──another time───► released (chosen): the participant picked a slot themselves
         ──cancel─────────► released (cancelled): the booking is cancelled
         ──deadline───────► released (expired): the booking still awaits a new time until
                            the lapse of ADR 0044
         ──withdraw───────► released (withdrawn): the planner took it back (ADR 0057 §10)

Silence is never consent: nothing moves without the participant's (or, after a phone call, the
planner's) confirmation. A proposal carries no personal data: the booking holds it.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class ProposalOutcome(StrEnum):
    """How a held proposal ended."""

    CONFIRMED = "confirmed"
    CHOSEN = "chosen"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class HeldProposal:
    """A proposed new time for one booking, holding a place in its slot while open.

    Attributes:
        id: Database id, ``None`` before it is stored.
        booking_id: The booking that has to move; it awaits a new time.
        slot_id: The proposed slot, created like a booking's when it did not exist yet.
        start: The proposed start (UTC).
        end: The proposed end (UTC).
        confirm_by: When the held place is released if nobody answered.
        created_at: When it was made.
        released_at: When it stopped holding the place, ``None`` while open.
        outcome: How it ended, ``None`` while open.
    """

    id: int | None
    booking_id: int
    slot_id: int
    start: datetime
    end: datetime
    confirm_by: datetime
    created_at: datetime
    released_at: datetime | None = None
    outcome: ProposalOutcome | None = None

    @property
    def is_open(self) -> bool:
        """Whether it still holds its place."""
        return self.released_at is None

    def is_due(self, now: datetime) -> bool:
        """Whether it is open and its deadline has passed."""
        return self.is_open and now >= self.confirm_by

    def is_current(self, pinned_start: datetime | None, now: datetime) -> bool:
        """Whether a message pinned to ``pinned_start`` is still about this open proposal."""
        return self.is_open and self.start == pinned_start and not self.is_due(now)

    def released(self, now: datetime, outcome: ProposalOutcome) -> "HeldProposal":
        """Return it released at ``now`` with ``outcome``."""
        return replace(self, released_at=now, outcome=outcome)

    def answered(self, new_start: datetime, now: datetime) -> "HeldProposal":
        """Return it released because the booking moved to ``new_start``: taken or not."""
        outcome = ProposalOutcome.CONFIRMED if new_start == self.start else ProposalOutcome.CHOSEN
        return self.released(now, outcome)


@dataclass(frozen=True)
class ProposalHours:
    """An organisation's hours for a proposal (ADR 0057 settings, class D).

    Attributes:
        confirm: ``reschedule_proposal_confirm_hours``: how long the place is held.
        reminder: ``reschedule_proposal_reminder_hours``: the reminder before the deadline;
            0 sends none.
    """

    confirm: int
    reminder: int


DEFAULT_PROPOSAL_HOURS = ProposalHours(confirm=48, reminder=12)
"""The settings' defaults."""


def proposal_deadline(
    now: datetime, confirm_hours: int, change_deadline: datetime, rebook_by: datetime
) -> datetime:
    """Return when an unanswered proposal is released (ADR 0057 point 7).

    ``reschedule_proposal_confirm_hours`` after ``now``, never beyond the proposed slot's change
    deadline (``Group.change_deadline``), so the participant can still move it after confirming,
    and never beyond ``rebook_by``, when the booking itself lapses (ADR 0044), so the deadline
    the message names holds.
    """
    return min(now + timedelta(hours=confirm_hours), change_deadline, rebook_by)


def proposal_reminder(now: datetime, confirm_by: datetime, reminder_hours: int) -> datetime | None:
    """Return when the one reminder goes, or ``None`` (setting 0, or the moment has passed)."""
    if reminder_hours <= 0:
        return None
    at = confirm_by - timedelta(hours=reminder_hours)
    return at if at > now else None
