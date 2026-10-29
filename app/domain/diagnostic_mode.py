"""Diagnostic mode of one group or organisation, as stored (ADR 0040, part 4).

Its own module so the persistence port can name it without importing the service.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DiagnosticMode:
    """Diagnostic mode for one target.

    Attributes:
        organisation: Organisation slug; for a group, the group's organisation.
        group: Group slug; ``None`` for the whole organisation.
        until: When the mode ends by itself (UTC).
        started_by: Subject of the superuser who switched it on.
    """

    organisation: str
    group: str | None
    until: datetime
    started_by: str

    def covers(self, organisation: str | None, group: str | None) -> bool:
        """Return whether an event about ``organisation`` / ``group`` falls under this mode."""
        if organisation != self.organisation:
            return False
        return self.group is None or self.group == group
