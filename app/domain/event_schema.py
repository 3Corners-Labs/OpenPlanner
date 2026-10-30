"""One event schema for the table, the log line, the CSV export and the log platform (ADR 0040).

``openplanner.event/2`` follows Elastic Common Schema and the OpenTelemetry log data model, so
a log platform needs no mapping: dotted field names (``event.action``, ``trace.id``), an
``@timestamp`` in RFC 3339 UTC with milliseconds, and ``log.level``. Our own ``outcome`` keeps
the five outcomes of the audit log; ``event.outcome`` is ECS's coarser success / failure /
unknown. A record holds ids, slugs and codes only; never personal data of citizens and never
an admin's display name (``actor`` is the identity provider's subject). Version 2 adds
``message``: the provider's own reason for a failed job (ADR 0065), masked and capped.

    AuditEvent + Envelope (version, installation) ─► to_record() ─► dict in FIELDS order

Adding, renaming or removing a field is a new schema version; ``docs/audit-events.md`` lists
the fields.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.audit import AuditEvent, Outcome

SCHEMA = "openplanner.event/2"
"""Name and version of the schema, sent with every record."""

FIELDS = (
    "schema",
    "event_id",
    "@timestamp",
    "log.level",
    "event.category",
    "event.action",
    "event.outcome",
    "outcome",
    "reason",
    "organisation",
    "group",
    "booking_id",
    "actor",
    "trace.id",
    "service.name",
    "service.version",
    "installation",
    "detail",
    "message",
)
"""Every field of a record, in the order the CSV export writes them."""

ECS_OUTCOME: dict[Outcome, str] = {
    Outcome.SUCCESS: "success",
    Outcome.SKIPPED: "unknown",
    Outcome.RETRY: "failure",
    Outcome.DENIED: "failure",
    Outcome.FAILURE: "failure",
}
"""ECS ``event.outcome`` per audit outcome."""

Value = str | int | None
"""What a record field holds."""


@dataclass(frozen=True)
class Envelope:
    """What every record of this installation carries besides the event itself.

    Attributes:
        service_version: The running release (``APP_VERSION``); empty in development.
        installation: Name of this installation (``INSTALLATION_NAME``), for platforms that
            receive several.
    """

    service_version: str = ""
    installation: str = ""


def rfc3339(at: datetime) -> str:
    """Write ``at`` as RFC 3339 in UTC with milliseconds and ``Z``.

    Raises:
        ValueError: For a naive datetime; every stored time carries its time zone.
    """
    if at.tzinfo is None:
        raise ValueError("naive datetime")
    utc = at.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def to_record(event: AuditEvent, envelope: Envelope) -> dict[str, Value]:
    """Return ``event`` as an ``openplanner.event/2`` record, fields in ``FIELDS`` order."""
    return {
        "schema": SCHEMA,
        "event_id": event.event_id,
        "@timestamp": rfc3339(event.at),
        "log.level": event.log_level.value,
        "event.category": event.event_category.value,
        "event.action": event.action,
        "event.outcome": ECS_OUTCOME[event.outcome],
        "outcome": event.outcome.value,
        "reason": event.reason,
        "organisation": event.organisation,
        "group": event.group,
        "booking_id": event.booking_id,
        "actor": event.actor,
        "trace.id": event.trace_id,
        "service.name": event.service,
        "service.version": envelope.service_version,
        "installation": envelope.installation,
        "detail": event.detail,
        "message": event.message,
    }
