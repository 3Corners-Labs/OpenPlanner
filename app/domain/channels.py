"""Notification channels and per-group reminder rules (ADR 0009).

A channel is data: each ``ChannelKind`` declares what a booking needs to be reachable on it.
Adding a channel means adding an enum value, its requirement and a ``MessageSender`` adapter;
planning and booking code stay unchanged. WhatsApp is on the roadmap: modelled, not enabled.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.domain.errors import DomainError, ErrorCode

if TYPE_CHECKING:  # avoid an import cycle; only used in annotations
    from app.domain.booking import Contact

MIN_REMINDER_MINUTES = 5
MAX_REMINDER_MINUTES = 30 * 24 * 60


class ChannelKind(StrEnum):
    """Ways to reach a participant."""

    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"


@dataclass(frozen=True)
class ChannelRequirement:
    """What a booking must provide before a message may be sent on a channel."""

    needs_email: bool
    needs_phone: bool
    needs_consent: bool


REQUIREMENTS: dict[ChannelKind, ChannelRequirement] = {
    ChannelKind.EMAIL: ChannelRequirement(needs_email=True, needs_phone=False, needs_consent=False),
    ChannelKind.SMS: ChannelRequirement(needs_email=False, needs_phone=True, needs_consent=False),
    ChannelKind.WHATSAPP: ChannelRequirement(
        needs_email=False, needs_phone=True, needs_consent=True
    ),
}


def reachable(kind: ChannelKind, contact: "Contact", consents: frozenset[ChannelKind]) -> bool:
    """Return whether ``contact`` can be messaged on ``kind``."""
    requirement = REQUIREMENTS[kind]
    return (
        (not requirement.needs_email or bool(contact.email))
        and (not requirement.needs_phone or contact.phone is not None)
        and (not requirement.needs_consent or kind in consents)
    )


@dataclass(frozen=True)
class ReminderRule:
    """Send a reminder on ``channel`` ``minutes_before`` the start (per group).

    Raises:
        DomainError: ``invalid_configuration`` if the offset is outside 5 min – 30 days.
    """

    channel: ChannelKind
    minutes_before: int
    enabled: bool = True

    def __post_init__(self) -> None:
        """Validate the offset."""
        if not MIN_REMINDER_MINUTES <= self.minutes_before <= MAX_REMINDER_MINUTES:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "minutes_before")
