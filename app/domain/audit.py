"""Audit events: who did what to which organisation or group, and with what outcome.

Written for every login, every denied authorisation decision and every change (ADR 0014),
and, as system events, for what the platform did on its own: every message, meeting, work
item and purge job with its result (ADR 0031). An event never contains personal data of
participants; the actor is the subject identifier from the identity provider, or ``system``. The
admin's display name lives once in their ``StaffMember`` record (ADR 0046) and is joined in
when the log is read, so events stay immutable and a name is stored in one place only.

An event sits at exactly one level: global (no organisation), an organisation (no group) or
a group. The audit log shows each admin the levels of their own scope and nothing above it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.event_context import current_service, current_trace_id, new_id


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


class Category(StrEnum):
    """What kind of event it is, for filtering, retention and the log platform (ADR 0040)."""

    AUDIT = "audit"
    """An admin action: login, denial, change."""
    SYSTEM = "system"
    """What the platform did on its own: jobs, storage checks."""
    SECURITY = "security"
    """A rejected webhook or a failing credential."""
    DIAGNOSTIC = "diagnostic"
    """Debug detail, only while diagnostic mode is on."""


class Level(StrEnum):
    """Log level, the syslog levels Python has (ADR 0040)."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


LEVEL_ORDER: tuple[Level, ...] = tuple(Level)
"""Levels from least to most severe."""


def at_least(level: Level) -> frozenset[Level]:
    """Return ``level`` and every more severe level."""
    return frozenset(LEVEL_ORDER[LEVEL_ORDER.index(level) :])


LEVEL_BY_OUTCOME: dict[Outcome, Level] = {
    Outcome.SUCCESS: Level.INFO,
    Outcome.SKIPPED: Level.INFO,
    Outcome.RETRY: Level.WARNING,
    Outcome.DENIED: Level.WARNING,
    Outcome.FAILURE: Level.ERROR,
}
"""The level of an audit or system event follows from its outcome."""


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
        message: The provider's own words on a failure (ADR 0065): Webex's reason with
            addresses masked, at most 300 characters, only for requests without participant data.
            Empty for every other event.
        source: Admin action or system event.
        booking_id: The booking a system event is about, if any (an id, not personal data).
        category: Kind of event; ``None`` follows the source (admin: audit, system: system).
        level: Log level; ``None`` follows the outcome (``LEVEL_BY_OUTCOME``).
        trace_id: The request or job that caused it; taken from the current context.
        service: ``web`` or ``worker``; taken from the current context.
        event_id: Unique id, so a copy on the log platform can be matched to the table.
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
    category: Category | None = None
    level: Level | None = None
    message: str = ""
    trace_id: str = field(default_factory=current_trace_id, compare=False)
    service: str = field(default_factory=current_service, compare=False)
    event_id: str = field(default_factory=new_id, compare=False)

    def __post_init__(self) -> None:
        """Fill in the derived category and level, so a stored event equals the one read back."""
        object.__setattr__(self, "category", self.event_category)
        object.__setattr__(self, "level", self.log_level)

    @property
    def event_category(self) -> Category:
        """The category, derived from the source when not set."""
        if self.category is not None:
            return self.category
        return Category.AUDIT if self.source is Source.ADMIN else Category.SYSTEM

    @property
    def log_level(self) -> Level:
        """The level, derived from the outcome when not set."""
        return self.level if self.level is not None else LEVEL_BY_OUTCOME[self.outcome]


class Subject(StrEnum):
    """A subject of the audit log: a fixed set of action prefixes, for the filter (ADR 0067)."""

    UPDATES = "updates"
    LOGIN = "login"
    SETTINGS = "settings"
    RIGHTS = "rights"
    BOOKINGS = "bookings"
    ORGANISATION = "organisation"
    INTEGRATIONS = "integrations"


SUBJECT_ACTIONS: Mapping[Subject, frozenset[str]] = {
    Subject.UPDATES: frozenset({"system.update", "system.version"}),
    Subject.LOGIN: frozenset({"admin.login", "admin.logout", "admin.session", "idp."}),
    Subject.SETTINGS: frozenset({"settings."}),
    Subject.RIGHTS: frozenset({"roles.", "role.", "rights.", "matrix.", "staff."}),
    Subject.BOOKINGS: frozenset({"bookings.", "booking.", "job.", "queue.", "schedule."}),
    Subject.ORGANISATION: frozenset(
        {
            "organisation.",
            "group.",
            "closure.",
            "branding.",
            "holidays.",
            "embedding.",
            "texts.",
            "reminders.",
        }
    ),
    Subject.INTEGRATIONS: frozenset(
        {"integrations.", "webhook.", "webex.", "api_clients.", "token."}
    ),
}
"""The action prefixes of each subject; an event belongs to a subject when its action starts
with one of them. The updates page filters on ``Subject.UPDATES``."""


@dataclass(frozen=True)
class AuditFilter:
    """What an admin narrowed the audit log to; always applied inside their scope.

    Attributes:
        source: Only admin actions or only system events; ``None`` for both.
        problems_only: Only denials, failures and retries.
        group: Only events of this group (slug).
        booking_id: Only events about this booking.
        actor: Only events of this actor (subject); the staff detail page (ADR 0046).
        min_level: Only events of this level or more severe (ADR 0040).
        category: Only events of this category (ADR 0040).
        since: Only events at or after this time (UTC).
        until: Only events before this time (UTC).
        trace_id: Only the events of this request or job: the whole chain (ADR 0040).
        actions: Only events whose action starts with one of these prefixes (a subject,
            ADR 0067); empty for all.
        action: Only events with this action, e.g. the request events of a booking (ADR 0068).
    """

    source: Source | None = None
    problems_only: bool = False
    group: str | None = None
    booking_id: int | None = None
    actor: str | None = None
    min_level: Level | None = None
    category: Category | None = None
    since: datetime | None = None
    until: datetime | None = None
    trace_id: str | None = None
    actions: frozenset[str] = frozenset()
    action: str | None = None

    def matches(self, event: AuditEvent) -> bool:
        """Return whether ``event`` passes the filter (the SQL adapter applies the same rules)."""
        return (
            (self.source is None or event.source is self.source)
            and (not self.problems_only or event.outcome in PROBLEMS)
            and (self.group is None or event.group == self.group)
            and (self.booking_id is None or event.booking_id == self.booking_id)
            and (self.actor is None or event.actor == self.actor)
            and (self.min_level is None or event.log_level in at_least(self.min_level))
            and (self.category is None or event.event_category is self.category)
            and (self.since is None or event.at >= self.since)
            and (self.until is None or event.at < self.until)
            and (self.trace_id is None or event.trace_id == self.trace_id)
            and (not self.actions or any(event.action.startswith(p) for p in self.actions))
            and (self.action is None or event.action == self.action)
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
