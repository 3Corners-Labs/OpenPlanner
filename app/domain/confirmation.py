"""When a participant must confirm a booking by email link, and when to remind them (ADR 0056).

A participant's booking starts as ``pending_confirmation``. It has to be confirmed by the earlier
of ``window`` after booking and ``cutoff`` before the start. When that lies less than
``min_lead`` ahead (the appointment is soon), the participant may still confirm until the change
deadline (start − dispatch lead), without a reminder. Reminders are spread evenly over the
window; one that would fall within ``min_lead`` of the deadline is dropped.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.errors import DomainError, ErrorCode


@dataclass(frozen=True)
class ConfirmPolicy:
    """An organisation's rules for confirming a booking (ADR 0056 settings).

    Attributes:
        window: ``booking_confirm_window_hours``: latest confirmation after booking.
        cutoff: ``booking_confirm_cutoff_hours``: latest confirmation before the start.
        reminders: ``booking_confirm_reminders``: how many reminders, evenly spread.
        min_lead: ``booking_confirm_min_minutes``: below this time to confirm the
            short-notice rule applies, and no reminder goes closer to the deadline.
    """

    window: timedelta
    cutoff: timedelta
    reminders: int
    min_lead: timedelta

    def __post_init__(self) -> None:
        """Refuse values the settings bounds never allow (a programming error).

        Raises:
            ValueError: the window or the minimum lead is not positive, or the cutoff or the
                number of reminders is negative.
        """
        if self.window <= timedelta(0) or self.min_lead <= timedelta(0):
            raise ValueError("window and min_lead must be positive")
        if self.cutoff < timedelta(0) or self.reminders < 0:
            raise ValueError("cutoff and reminders must not be negative")

    @classmethod
    def from_settings(
        cls, *, window_hours: int, cutoff_hours: int, reminders: int, min_minutes: int
    ) -> "ConfirmPolicy":
        """Return the policy for the ADR 0056 settings, given in their own units."""
        return cls(
            window=timedelta(hours=window_hours),
            cutoff=timedelta(hours=cutoff_hours),
            reminders=reminders,
            min_lead=timedelta(minutes=min_minutes),
        )


DEFAULT_CONFIRM_POLICY = ConfirmPolicy.from_settings(
    window_hours=24, cutoff_hours=2, reminders=1, min_minutes=15
)
"""The settings' defaults (ADR 0056), for services built without the live settings."""


@dataclass(frozen=True)
class ConfirmWindow:
    """The deadline to confirm one booking and the moments of its reminders.

    Attributes:
        confirm_by: The booking lapses when it is not confirmed by then.
        reminders: When the reminders go, in time order.
        short_notice: Whether the appointment was too soon for the normal window.
    """

    confirm_by: datetime
    reminders: tuple[datetime, ...]
    short_notice: bool = False


def confirm_window(
    *, now: datetime, start: datetime, dispatch_lead: timedelta, policy: ConfirmPolicy
) -> ConfirmWindow:
    """Return the confirmation deadline and reminder moments of a booking made at ``now``.

    Raises:
        DomainError: ``slot_unavailable`` when even the change deadline has passed.
    """
    confirm_by = min(now + policy.window, start - policy.cutoff)
    if confirm_by - now < policy.min_lead:
        change_deadline = start - dispatch_lead
        if change_deadline <= now:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        return ConfirmWindow(change_deadline, (), short_notice=True)
    step = (confirm_by - now) / (policy.reminders + 1)
    moments = (now + step * i for i in range(1, policy.reminders + 1))
    return ConfirmWindow(confirm_by, tuple(m for m in moments if confirm_by - m >= policy.min_lead))
