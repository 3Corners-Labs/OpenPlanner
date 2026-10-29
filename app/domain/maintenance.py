"""Maintenance mode of the whole installation (ADR 0038): the state and its rules.

Maintenance is a technical state of the platform, not a business decision about a group (that
is an emergency closure, ADR 0024). While it is on, data is frozen: citizens cannot book and
admins can change only what ``authorization.MAINTENANCE_WRITABLE`` allows. Integrations,
webhooks, confirmed appointments and the privacy jobs keep running.

It is on when either says so:

    maintenance_state row   switched on /admin/maintenance (system.maintenance) or by the CLI
    MAINTENANCE_MODE=true   the environment, for work while the database is unavailable;
                            the portal shows it and cannot switch it off

There is no automatic end: the expected end is information only (ADR 0038 §6). Maintenance
that stays on longer than the alert threshold is reported as a system event.

Nothing here is about citizens: no personal data.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode

ON_DETAIL = "maintenance.on"
OFF_DETAIL = "maintenance.off"
"""Audit details of switching maintenance on or off (action ``system.maintenance``)."""

REFUSED_DETAIL = "maintenance.refused"
"""Audit detail of a change refused because maintenance is on (action: its permission)."""

OVERDUE_EVENT = "maintenance.overdue"
"""System event for maintenance that has been on longer than the alert threshold."""


class MaintenanceReason(StrEnum):
    """Why maintenance is on; a code, shown translated, never free text."""

    UPDATE = "update"
    RESTORE = "restore"
    INTEGRATION = "integration"
    INCIDENT = "incident"
    OTHER = "other"


@dataclass(frozen=True)
class MaintenanceState:
    """The stored switch (the ``maintenance_state`` row).

    Attributes:
        on: Whether maintenance is switched on in the database.
        since: When it was switched on (UTC).
        by: Who switched it on: an admin's subject identifier or a host actor
            (``host-updater``, ``host-cli``); never a name.
        expected_end: When the operator expects to be done (information only).
        reason: Why it is on.
    """

    on: bool
    since: datetime | None = None
    by: str = ""
    expected_end: datetime | None = None
    reason: MaintenanceReason | None = None


OFF = MaintenanceState(on=False)
"""The state of an installation that never had maintenance."""


@dataclass(frozen=True)
class MaintenanceStatus:
    """What the installation is in now: the stored switch and the environment override.

    Attributes:
        state: The stored switch; ``OFF`` when only the environment says so and the database
            was not read.
        environment: Whether ``MAINTENANCE_MODE`` switches it on.
    """

    state: MaintenanceState
    environment: bool = False

    @property
    def active(self) -> bool:
        """Whether maintenance is on, from either source."""
        return self.environment or self.state.on


def switched_on(
    now: datetime,
    by: str,
    reason: MaintenanceReason,
    expected_end: datetime | None,
    current: MaintenanceState,
) -> MaintenanceState:
    """Return the state after switching maintenance on.

    Switching on while it is already on keeps the original ``since`` and ``by`` (the start of
    the window) and replaces the expected end and the reason.

    Raises:
        DomainError: ``invalid_configuration`` on field ``expected_end`` when it is not in the
            future.
    """
    if expected_end is not None and expected_end <= now:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "expected_end")
    if current.on:
        return MaintenanceState(True, current.since, current.by, expected_end, reason)
    return MaintenanceState(True, now, by, expected_end, reason)


def overdue(status: MaintenanceStatus, now: datetime, limit: timedelta) -> bool:
    """Return whether the stored switch has been on for longer than ``limit``.

    The environment override has no start time, so only the stored switch is judged.
    """
    since = status.state.since
    return status.state.on and since is not None and now - since > limit
