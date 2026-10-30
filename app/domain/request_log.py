"""Every call to Webex and WxCC as a system event (ADR 0068): the request log.

The HTTP adapter reports each request it made through the ``RequestLog`` port; this module
turns it into a system event ``integration.request`` at the level the current trace is bound
to (the booking's organisation and group, and the booking itself when a job or a direct call
is running). The event carries codes and numbers only: method, path without its query, status,
duration, Webex's tracking id and the version that made the call. Never a body or a header.

    AuthorizedClient.request ─► RequestLog.record(RequestRecord)
                              ─► AuditEvent(integration.request, booking_id, detail=...)

The booking page lists these events; the audit log's chain view shows them under the job
that made them (same trace id); the log platform receives them with the other system events.
``EventRetention.requests`` prunes them apart from the rest (``SystemLog.prune``).
"""

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.event_context import current
from app.domain.ports import Clock, RequestRecord, Transaction, UnitOfWork

REQUEST_EVENT = "integration.request"
"""Action of a request event."""

PATH_LIMIT = 80
"""Longest path kept in the event's detail; longer ones are cut, never a query string."""
TRACKING_LIMIT = 48
"""Longest tracking id kept."""


def request_detail(record: RequestRecord, version: str) -> str:
    """Return the event's ``detail``: machine keys and values, within the column's size."""
    return ",".join(
        (
            f"method:{record.method}",
            f"path:{record.path[:PATH_LIMIT]}",
            f"status:{record.status}",
            f"ms:{record.duration_ms}",
            f"tracking:{record.tracking_id[:TRACKING_LIMIT]}",
            f"version:{version}",
        )
    )


def request_fields(detail: str) -> dict[str, str]:
    """Return the keys of a request event's ``detail`` (``method``, ``path``, ...) for a page."""
    fields: dict[str, str] = {}
    for part in detail.split(","):
        key, _, value = part.partition(":")
        fields[key] = value
    return fields


class RequestEvents:
    """``RequestLog`` that writes system events.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        version: ``APP_VERSION`` of this process, stamped into every event's detail.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, version: str = "") -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._version = version

    async def record(self, record: RequestRecord) -> None:
        """Write one ``integration.request`` event at the level of the current trace."""
        context = current()
        async with self._uow.locked() as tx:
            organisation, group = context.organisation, context.group
            if context.booking_id is not None:  # the booking decides its level, not the path
                organisation, group = await _booking_level(
                    tx, context.booking_id, organisation, group
                )
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    SYSTEM_ACTOR,
                    REQUEST_EVENT,
                    Outcome.SUCCESS if not record.error else Outcome.FAILURE,
                    record.error,
                    organisation,
                    group,
                    request_detail(record, self._version),
                    Source.SYSTEM,
                    context.booking_id,
                )
            )


async def _booking_level(
    tx: Transaction, booking_id: int, organisation: str | None, group_slug: str | None
) -> tuple[str | None, str | None]:
    """Return the organisation and group slugs of the booking's group.

    A public page binds only the group slug of its path; the organisation must be there too,
    or the organisation's admins would not see the event (``Scope.contains``). Without a
    stored booking the level of the trace stays as it is.
    """
    booking = await tx.get_booking(booking_id)
    group = await tx.get_group_by_id(booking.group_id) if booking else None
    summary = await tx.get_group_summary(group.slug) if group else None
    if summary is None:
        return organisation, group_slug
    return summary.organisation_slug, summary.group.slug
