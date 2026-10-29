"""Audit events: who did what to which organisation or group, and with what outcome.

Written for every login, every denied authorisation decision and every change (ADR 0014),
and, as system events, for what the platform did on its own: every message, meeting, work
item and purge job with its result (ADR 0031). An event never contains personal data of
citizens; the actor is the subject identifier from the identity provider, or ``system``. The
admin's display name lives once in their ``StaffMember`` record (ADR 0046) and is joined in
when the log is read, so events stay immutable and a name is stored in one place only.

An event sits at exactly one level: global (no organisation), an organisation (no group) or
a group. The audit log shows each admin the levels of their own scope and nothing above it.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Outcome(StrEnum):
    """Result of the audited action."""

    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"
    RETRY = "retry"
    """Failed, and will be tried again (system events)."""
    SKIPPED = "skipped"
    """Deliberately not done, e.g. a reminder for a booking that moved (system events)."""


PROBLEMS = frozenset({Outcome.DENIED, Outcome.FAILURE, Outcome.RETRY})
"""Outcomes the "problems only" filter of the audit log keeps."""


class Source(StrEnum):
    """Who caused the event: an admin (or a visitor trying to log in) or the platform."""

    ADMIN = "admin"
    SYSTEM = "system"


SYSTEM_ACTOR = "system"
"""Actor recorded for system events."""


@dataclass(frozen=True)
class AuditEvent:
    """One audited action.

    Attributes:
        at: When it happened (UTC).
        actor: Subject identifier of the admin, or ``anonymous``.
        action: A permission (``group.view``) or an event key (``admin.login``).
        outcome: Success, denied or failure.
        reason: Machine-readable reason (an authorisation ``Reason`` or an error code).
        organisation: Slug of the organisation concerned, if any.
        group: Slug of the group concerned, if any.
        detail: What changed, as machine keys (changed field names, ``rule.add``); never
            values, so no personal data.
        source: Admin action or system event.
        booking_id: The booking a system event is about, if any (an id, not personal data).
    """

    at: datetime
    actor: str
    action: str
    outcome: Outcome
    reason: str = ""
    organisation: str | None = None
    group: str | None = None
    detail: str = ""
    source: Source = Source.ADMIN
    booking_id: int | None = None


@dataclass(frozen=True)
class AuditFilter:
    """What an admin narrowed the audit log to; always applied inside their scope.

    Attributes:
        source: Only admin actions or only system events; ``None`` for both.
        problems_only: Only denials, failures and retries.
        group: Only events of this group (slug).
        booking_id: Only events about this booking.
        actor: Only events of this actor (subject); the staff detail page (ADR 0046).
    """

    source: Source | None = None
    problems_only: bool = False
    group: str | None = None
    booking_id: int | None = None
    actor: str | None = None

    def matches(self, event: AuditEvent) -> bool:
        """Return whether ``event`` passes the filter (the SQL adapter applies the same rules)."""
        return (
            (self.source is None or event.source is self.source)
            and (not self.problems_only or event.outcome in PROBLEMS)
            and (self.group is None or event.group == self.group)
            and (self.booking_id is None or event.booking_id == self.booking_id)
            and (self.actor is None or event.actor == self.actor)
        )


@dataclass(frozen=True)
class AuditEntry:
    """An audit event as the audit log shows it.

    Attributes:
        event: The stored event.
        actor_name: The actor's latest known display name; ``None`` if unknown.
    """

    event: AuditEvent
    actor_name: str | None = None
