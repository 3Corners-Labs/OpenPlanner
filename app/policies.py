"""One organisation's settings as the policy objects the domain services take.

The web process and the worker build the same services from the same live settings; the
conversions live here once, so neither has a second copy (ADR 0005). Each takes the settings
of one organisation (``LiveSettings.for_organisation``).
"""

from app.config import Settings
from app.domain.confirmation import ConfirmPolicy
from app.domain.rebooking import Reach
from app.domain.reschedule_proposal import ProposalHours


def confirm_policy(settings: Settings) -> ConfirmPolicy:
    """Return the rules for confirming a booking from one organisation's settings (ADR 0056)."""
    return ConfirmPolicy.from_settings(
        window_hours=settings.booking_confirm_window_hours,
        cutoff_hours=settings.booking_confirm_cutoff_hours,
        reminders=settings.booking_confirm_reminders,
        min_minutes=settings.booking_confirm_min_minutes,
    )


def reach(settings: Settings) -> Reach:
    """Return how far proposed and suggested times may lie, from one organisation's settings."""
    return Reach(settings.reschedule_search_days, settings.reschedule_time_tolerance_minutes)


def proposal_hours(settings: Settings) -> ProposalHours:
    """Return how long a proposal is held and when it is recalled (ADR 0057)."""
    return ProposalHours(
        settings.reschedule_proposal_confirm_hours, settings.reschedule_proposal_reminder_hours
    )
