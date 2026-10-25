"""Ports: Protocols for everything outside the domain.

Adapters in ``app.adapters`` implement them; tests use the fakes in ``tests/fakes``.
External systems still to come with their work package: ``VideoProvider`` (WP5, including
live meeting status per ADR 0010), ``ContactCenterDispatcher`` (WP6, ADR 0007),
``IdentityProvider`` (WP7).
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.domain.audit import AdminProfile, AuditEntry, AuditEvent
from app.domain.authorization import Principal, Scope
from app.domain.booking import Booking, MeetingRef, Slot, SlotUsage
from app.domain.branding import Branding, Logo
from app.domain.calls import (
    BookingEvent,
    BookingOverview,
    CallStatus,
    MeetingRequest,
    WorkItem,
)
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.job_plan import PlannedJob
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)


class Clock(Protocol):
    """Source of the current time. Injected so that time-based rules are testable."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


@dataclass(frozen=True)
class Attachment:
    """A file attached to a message, e.g. the ``.ics`` calendar entry."""

    filename: str
    content_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class OutboundMessage:
    """A message to one citizen. Contains personal data: keep it in memory only."""

    channel: ChannelKind
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    html: str | None = field(default=None, repr=False)
    attachments: tuple[Attachment, ...] = ()


class MessageSender(Protocol):
    """Delivers messages on one channel (ADR 0009): SMTP for email, later SMS and WhatsApp."""

    async def send(self, message: OutboundMessage) -> None:
        """Deliver ``message``; raise on failure so the job is retried."""
        ...


@dataclass(frozen=True)
class LoginStart:
    """Where to send an admin to log in, and the secrets to check the answer with.

    ``state``, ``nonce`` and ``code_verifier`` are kept in the signed session until the
    identity provider redirects back; they are never logged.
    """

    url: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)


class IdentityError(Exception):
    """Login failed; ``code`` says why (``token_exchange_failed``, ``invalid_token``, …)."""

    def __init__(self, code: str) -> None:
        """Store the code; it is also the message."""
        super().__init__(code)
        self.code = code


class IntegrationError(Exception):
    """A call to Webex or WxCC failed; ``code`` says why, ``retryable`` whether to try again.

    The code is a short machine key (``unauthorized``, ``rate_limited``, ``server_error``,
    ``rejected``); it never contains response bodies, which could hold personal data.
    """

    def __init__(self, code: str, *, retryable: bool) -> None:
        """Store the code and whether a retry can help."""
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class VideoProvider(Protocol):
    """Schedules and removes video meetings (Webex Meetings API, ADR 0017)."""

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        """Schedule a meeting and return its id and join link.

        Raises:
            IntegrationError: If the provider refuses or cannot be reached.
        """
        ...

    async def delete_meeting(self, meeting_id: str, host_email: str) -> None:
        """Delete a meeting; deleting one that no longer exists is not an error."""
        ...


class ContactCenterDispatcher(Protocol):
    """Creates work items in Webex Contact Center (Create Task API v2, ADR 0018)."""

    async def create_task(self, item: WorkItem) -> str:
        """Create the work item and return the task id.

        Raises:
            IntegrationError: If WxCC refuses or cannot be reached.
        """
        ...


class IdentityProvider(Protocol):
    """The admin portal's identity provider (OIDC; PingFederate in production, ADR 0013)."""

    async def start_login(self, redirect_uri: str) -> LoginStart:
        """Prepare an authorisation request with fresh ``state``, ``nonce`` and PKCE."""
        ...

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        """Exchange the code, verify the ID token and return the admin it describes.

        Raises:
            IdentityError: If the exchange or any token check fails.
        """
        ...

    async def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        """Return where to end the session at the provider, or ``None`` if it has no logout.

        Without this, the provider's single sign-on would log the admin straight back in.
        """
        ...


class Transaction(Protocol):
    """Everything the domain reads and writes within one database transaction.

    Every ``list_*`` method takes a ``Scope`` and returns only what it covers (ADR 0014); a
    test enforces this for new methods.
    """

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        """Return the groups inside ``scope``, ordered by organisation and name."""
        ...

    async def get_group_summary(self, slug: str) -> GroupSummary | None:
        """Return one group with its organisation, for an authorisation decision."""
        ...

    async def add_audit_event(self, event: AuditEvent) -> None:
        """Store an audit event."""
        ...

    async def list_audit_events(self, scope: Scope, limit: int = 100) -> list[AuditEntry]:
        """Return the newest audit events about organisations and groups inside ``scope``.

        Each entry carries the actor's display name from their ``AdminProfile``, if any.
        """
        ...

    async def save_admin_profile(self, profile: AdminProfile) -> None:
        """Create or replace the profile of ``profile.subject``."""
        ...

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        ...

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        ...

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return all rules of the group and its overrides between the two dates."""
        ...

    async def list_organisations(self, scope: Scope) -> list[Organisation]:
        """Return the organisations inside ``scope``, ordered by slug."""
        ...

    async def get_organisation(self, slug: str) -> Organisation | None:
        """Return the organisation with ``slug``, with its branding."""
        ...

    async def get_organisation_by_id(self, organisation_id: int) -> Organisation | None:
        """Return the organisation with ``organisation_id``, with its branding."""
        ...

    async def add_organisation(self, organisation: Organisation) -> Organisation:
        """Insert an organisation (default branding) and return it with its ID."""
        ...

    async def save_organisation(self, organisation: Organisation) -> None:
        """Store name and default locale; slug and branding are left alone."""
        ...

    async def save_branding(self, organisation_id: int, branding: Branding) -> None:
        """Store display name and accent colour (the logo is stored by ``save_logo``)."""
        ...

    async def get_logo(self, organisation_slug: str) -> Logo | None:
        """Return the organisation's logo, if it has one."""
        ...

    async def save_logo(self, organisation_id: int, logo: Logo | None) -> None:
        """Store or, with ``None``, remove the organisation's logo."""
        ...

    async def add_group(self, group: Group) -> Group:
        """Store a new group and return it with its id; the slug must be free."""
        ...

    async def save_group(self, group: Group) -> None:
        """Store the changed settings of an existing group (its slug never changes)."""
        ...

    async def count_future_bookings(self, group_id: int, now: datetime) -> int:
        """Return how many confirmed bookings of the group start after ``now``."""
        ...

    async def get_schedule_items(
        self, group_id: int, from_day: date | None = None
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return the group's rules and overrides (from ``from_day`` on), each with its id."""
        ...

    async def add_schedule_rule(self, group_id: int, rule: ScheduleRule) -> int:
        """Store a rule for the group and return its id."""
        ...

    async def delete_schedule_rule(self, group_id: int, rule_id: int) -> bool:
        """Delete the group's rule ``rule_id``; ``False`` if the group has no such rule."""
        ...

    async def add_schedule_override(self, group_id: int, override: ScheduleOverride) -> int:
        """Store an override for the group and return its id."""
        ...

    async def delete_schedule_override(self, group_id: int, override_id: int) -> bool:
        """Delete the group's override ``override_id``; ``False`` if it has no such override."""
        ...

    async def save_meeting(self, booking_id: int, meeting: MeetingRef) -> None:
        """Store the booking's meeting id and join link."""
        ...

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        """Store the booking's WxCC task id."""
        ...

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        """Return the booking whose meeting has ``meeting_id``."""
        ...

    async def get_booking_by_task(self, task_id: str) -> Booking | None:
        """Return the booking whose WxCC task has ``task_id``."""
        ...

    async def add_booking_event(self, event: BookingEvent) -> None:
        """Append an event to a booking's timeline."""
        ...

    async def get_booking_events(self, booking_id: int) -> list[BookingEvent]:
        """Return a booking's timeline, oldest first (access is checked by the caller)."""
        ...

    async def list_bookings(
        self, scope: Scope, group_id: int, lower: datetime, upper: datetime
    ) -> list[BookingOverview]:
        """Return the group's bookings with lower <= start < upper and their call status.

        The group must lie inside ``scope`` (ADR 0014); otherwise the list is empty.
        """
        ...

    async def get_call_status(self, booking_id: int) -> CallStatus | None:
        """Return the booking's aggregated call status, if anything happened yet."""
        ...

    async def save_call_status(self, status: CallStatus) -> None:
        """Create or replace the booking's aggregated call status."""
        ...

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        ...

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and active bookings of existing slots with lower <= start < upper."""
        ...

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        ...

    async def add_slot(self, slot: Slot) -> Slot:
        """Store a new slot and return it with its ID."""
        ...

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of confirmed bookings in the slot."""
        ...

    async def add_booking(self, booking: Booking) -> Booking:
        """Store a new booking and return it with its ID."""
        ...

    async def get_booking(self, booking_id: int) -> Booking | None:
        """Return the booking with ``booking_id``."""
        ...

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        """Set every PII column of the booking to NULL and record ``pii_purged_at``."""
        ...

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        ...

    async def save_booking(self, booking: Booking) -> None:
        """Persist changes to an existing booking (status, cancelled_at)."""
        ...

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and store ``jobs`` instead (CLAUDE.md)."""
        ...


class UnitOfWork(Protocol):
    """Opens transactions. Leaving the context commits; an exception rolls back."""

    def read(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a read transaction; it never blocks or is blocked by writers."""
        ...

    def locked(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a write transaction holding the database write lock from the start.

        On SQLite this is ``BEGIN IMMEDIATE``: two bookings can never both see the last
        free place. Raises ``DomainError(database_busy)`` if the lock cannot be obtained.
        """
        ...
