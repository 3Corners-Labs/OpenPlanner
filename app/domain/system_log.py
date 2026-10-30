"""System events: what the platform did on its own, for troubleshooting per level (ADR 0031).

The worker records every job it runs (message, meeting, work item, purge) as a system event
in the audit log. An event about a booking is placed at that booking's group, so the
group's admins, the organisation's admins and the superuser see it, and nobody else. An event
about a group itself (the automatic integration check, ADR 0065) is placed at that group the
same way. An event without a known booking or group is global and only the superuser sees it.

    record(action, outcome, booking_id)  ─► booking ─► group ─► organisation slug
    record(action, outcome, group_slug=) ─► group ─► organisation slug
                                         ─► AuditEvent(source=system) at that level

System events carry codes only (job type, error code, attempt, channel): no personal data, no
message text, no addresses. Every category of event is pruned after its own retention period
(ADR 0040, part 4): diagnostic 7 days, system 90 days, security 1 year, admin actions 2 years
by default, each a setting.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Category, Outcome, Source
from app.domain.event_context import bind_level
from app.domain.ports import Clock, Transaction, UnitOfWork


@dataclass(frozen=True)
class EventRetention:
    """How long each category of event is kept (ADR 0040, part 4).

    Attributes:
        audit: Admin actions (``AUDIT_RETENTION_DAYS``; product owner, 2026-10-29).
        system: System events (``SYSTEM_EVENT_RETENTION_DAYS``).
        security: Security events (``SECURITY_EVENT_RETENTION_DAYS``).
        diagnostic: Debug events (``DIAGNOSTIC_EVENT_RETENTION_DAYS``).
    """

    audit: timedelta = timedelta(days=730)
    system: timedelta = timedelta(days=90)
    security: timedelta = timedelta(days=365)
    diagnostic: timedelta = timedelta(days=7)

    def of(self, category: Category) -> timedelta:
        """Return the retention period of ``category``."""
        return {
            Category.AUDIT: self.audit,
            Category.SYSTEM: self.system,
            Category.SECURITY: self.security,
            Category.DIAGNOSTIC: self.diagnostic,
        }[category]


class SystemLog:
    """Writes and prunes system events.

    Args:
        uow: Opens database transactions.
        clock: Current time.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

    async def record(
        self,
        action: str,
        outcome: Outcome,
        *,
        booking_id: int | None = None,
        group_slug: str | None = None,
        reason: str = "",
        detail: str = "",
        message: str = "",
    ) -> None:
        """Write one system event at the level of ``booking_id``'s group or of ``group_slug``.

        Without either the event is global.

        Args:
            action: Event key, e.g. ``job.send_reminder``.
            outcome: Success, retry, failure or skipped.
            booking_id: The booking the event is about, if any.
            group_slug: The group the event is about, when it is about no booking.
            reason: Machine-readable reason or error code.
            detail: Further machine keys, e.g. ``attempt:2,channel:sms``.
            message: The provider's own reason for a failure (ADR 0065), if it gave one.
        """
        async with self._uow.locked() as tx:
            organisation, group = await _level(tx, booking_id, group_slug)
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    SYSTEM_ACTOR,
                    action,
                    outcome,
                    reason,
                    organisation,
                    group,
                    detail,
                    Source.SYSTEM,
                    booking_id,
                    message=message,
                )
            )

    async def bind_level(self, booking_id: int | None) -> None:
        """Tag the current trace with the organisation and group of ``booking_id`` (ADR 0040)."""
        async with self._uow.read() as tx:
            bind_level(*await _level(tx, booking_id))

    async def prune(self, retention: EventRetention) -> int:
        """Delete every event older than its category's retention; return how many."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            removed = 0
            for category in Category:
                removed += await tx.delete_audit_events(category, now - retention.of(category))
            return removed


async def _level(
    tx: Transaction, booking_id: int | None, group_slug: str | None = None
) -> tuple[str | None, str | None]:
    """Return the organisation and group slugs of a booking or a group, if known."""
    booking = await tx.get_booking(booking_id) if booking_id is not None else None
    group = await tx.get_group_by_id(booking.group_id) if booking is not None else None
    slug = group.slug if group is not None else group_slug
    summary = await tx.get_group_summary(slug) if slug is not None else None
    if summary is None:
        return None, None
    return summary.organisation_slug, summary.group.slug
