"""System events: what the platform did on its own, for troubleshooting per level (ADR 0031).

The worker records every job it runs (message, meeting, work item, purge) as a system event
in the audit log. An event about a booking is placed at that booking's group, so the
group's admins, the organisation's admins and the superuser see it, and nobody else. An event
without a known booking is global and only the superuser sees it.

    record(action, outcome, booking_id) ─► booking ─► group ─► organisation slug
                                        ─► AuditEvent(source=system) at that level

System events carry codes only (job type, error code, attempt, channel): no personal data, no
message text, no addresses. They are pruned after a retention period; admin actions are kept.
"""

from datetime import timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.event_context import bind_level
from app.domain.ports import Clock, Transaction, UnitOfWork


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
        reason: str = "",
        detail: str = "",
    ) -> None:
        """Write one system event at the level of ``booking_id``'s group (global without one).

        Args:
            action: Event key, e.g. ``job.send_reminder``.
            outcome: Success, retry, failure or skipped.
            booking_id: The booking the event is about, if any.
            reason: Machine-readable reason or error code.
            detail: Further machine keys, e.g. ``attempt:2,channel:sms``.
        """
        async with self._uow.locked() as tx:
            organisation, group = await _level(tx, booking_id)
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
                )
            )

    async def bind_level(self, booking_id: int | None) -> None:
        """Tag the current trace with the organisation and group of ``booking_id`` (ADR 0040)."""
        async with self._uow.read() as tx:
            bind_level(*await _level(tx, booking_id))

    async def prune(self, retention: timedelta) -> int:
        """Delete system events older than ``retention``; return how many were removed."""
        async with self._uow.locked() as tx:
            return await tx.delete_audit_events(Source.SYSTEM, self._clock.now() - retention)


async def _level(tx: Transaction, booking_id: int | None) -> tuple[str | None, str | None]:
    """Return the organisation and group slugs of a booking; ``(None, None)`` if unknown."""
    booking = await tx.get_booking(booking_id) if booking_id is not None else None
    group = await tx.get_group_by_id(booking.group_id) if booking is not None else None
    summary = await tx.get_group_summary(group.slug) if group is not None else None
    if summary is None:
        return None, None
    return summary.organisation_slug, summary.group.slug
