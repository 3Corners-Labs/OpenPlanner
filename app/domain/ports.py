"""Ports: Protocols for every external system the domain depends on.

Adapters in ``app.adapters`` implement them; tests use the fakes in ``tests/fakes``.
Further ports (VideoProvider, ContactCenterDispatcher, MailSender, SmsSender,
IdentityProvider) are added in the work package that introduces the integration.
"""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time. Injected so that time-based rules are testable."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...
