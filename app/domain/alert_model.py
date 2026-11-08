"""The values of alert delivery (ADR 0082 point 9): channel kinds, targets, delivery state.

Kept apart from ``app.domain.alert_delivery`` so the ``Transaction`` and ``AlertSender`` ports
can name them. None of it holds participant data; a target's address is an administrator's
email address or a Webex room id.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode

_EMAIL = re.compile(r"^[^@\s<>\",;]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_ROOM = re.compile(r"^[A-Za-z0-9_=-]{10,200}$")
TARGET_LEVELS = ("warning", "outage")
"""The minimum levels a target may choose; ``info`` alerts are never delivered."""


class AlertChannelKind(StrEnum):
    """Where an alert target delivers (ADR 0082 point 9); a third kind needs an ADR."""

    EMAIL = "email"
    WEBEX_SPACE = "webex_space"


@dataclass(frozen=True)
class AlertTarget:
    """One place alerts are delivered to.

    Attributes:
        id: Stored id; ``None`` before it is stored.
        organisation_id: The organisation whose alerts it gets; ``None`` for the platform's.
        kind: Email or a Webex space.
        address: The email address, or the Webex room id.
        min_level: The least urgent level it gets: ``warning`` or ``outage`` (an
            ``AlertLevel`` value; kept a string so this module needs nothing of the alerts).
    """

    id: int | None
    organisation_id: int | None
    kind: AlertChannelKind
    address: str
    min_level: str = "warning"

    def __post_init__(self) -> None:
        """Validate the address for its kind and the level."""
        pattern = _EMAIL if self.kind is AlertChannelKind.EMAIL else _ROOM
        if not pattern.fullmatch(self.address):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "address")
        if self.min_level not in TARGET_LEVELS:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "min_level")


@dataclass(frozen=True)
class DeliveryState:
    """What was sent of one alert to one target.

    Attributes:
        token: The streak and level that was last sent (``Alert.token``).
        sent_at: When it was last delivered, ``None`` while it never was.
        attempts: Failed tries of the pending delivery.
        retry_at: When the next try may run after a failure (after the last retry: the repeat).
    """

    target_id: int
    key: str
    token: str = ""
    sent_at: datetime | None = None
    attempts: int = 0
    retry_at: datetime | None = None


@dataclass(frozen=True)
class RenderedAlert:
    """An alert written out for a channel: a subject and plain text, and markdown for a space."""

    subject: str
    text: str
    markdown: str
