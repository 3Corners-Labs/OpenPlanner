"""One event schema for the table, the log line, the CSV export and the log platform (ADR 0040).

``openplanner.event/3`` follows Elastic Common Schema and the OpenTelemetry log data model, so
a log platform needs no mapping: dotted field names (``event.action``, ``trace.id``), an
``@timestamp`` in RFC 3339 UTC with milliseconds, and ``log.level``. Our own ``outcome`` keeps
the five outcomes of the audit log; ``event.outcome`` is ECS's coarser success / failure /
unknown. A record holds ids, slugs and codes only; never personal data of participants and never
an admin's display name (``actor`` is the identity provider's subject).

Version 3 (ADR 0103) makes the line explain itself: ``message`` is the readable sentence of the
action from the register (``app.domain.log_events``), the provider's reason for a failed job
(ADR 0065) moves to ``error.message``, ``actor.type`` and ``actor.role`` say what kind of actor
it was without naming it, and the parts of ``detail`` are also fields of their own,
``labels.<name>``. Ordinary log lines (``app.logging``) carry the same envelope.

    AuditEvent + Envelope (version, installation) ─► to_record() ─► FIELDS, then labels.*

``detail`` keeps its form, which the portal reads: parts separated by commas, each a
``name:value`` or a bare flag (``horizon_days,name``, ``update.request,from:0.3.0,to:0.4.0``).
``detail_labels`` turns it into fields: ``labels.from: "0.3.0"``, and the flags together as
``labels.what``.

Adding, renaming or removing a field is a new schema version; ``docs/audit-events.md`` lists
the fields.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.audit import AuditEvent, Outcome
from app.domain.log_events import describe_event

SCHEMA = "openplanner.event/3"
"""Name and version of the schema, sent with every record and every log line."""

FIELDS = (
    "@timestamp",
    "log.level",
    "event.category",
    "event.action",
    "message",
    "outcome",
    "event.outcome",
    "reason",
    "organisation",
    "group",
    "booking_id",
    "actor",
    "actor.type",
    "actor.role",
    "error.message",
    "detail",
    "trace.id",
    "event_id",
    "service.name",
    "service.version",
    "installation",
    "schema",
)
"""Every fixed field of a record, in reading order: what a person reads first (when, how
severe, what, the sentence) leads, the plumbing follows. The CSV export writes them in this
order; a record adds its ``labels.*`` after these (the CSV keeps them in ``detail``)."""

LABELS = "labels."
"""Prefix of the fields taken from ``detail`` and from a log call's own fields (ECS ``labels``)."""

FLAGS = "what"
"""The label that holds the bare flags of a detail, comma-separated."""

_LABEL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")
"""What may be a label name in a detail part ``name:value``."""

ECS_OUTCOME: dict[Outcome, str] = {
    Outcome.SUCCESS: "success",
    Outcome.SKIPPED: "unknown",
    Outcome.RETRY: "failure",
    Outcome.DENIED: "failure",
    Outcome.FAILURE: "failure",
}
"""ECS ``event.outcome`` per audit outcome."""

Value = str | int | float | None
"""What a record field holds."""


def label_name(name: str) -> str:
    """Return ``name`` as a flat label key: dots and dashes become underscores."""
    return name.replace(".", "_").replace("-", "_")


def label_value(value: object) -> str | int | float:
    """Return ``value`` as a label holds it: a number stays one, anything else is text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and value.isdigit() and len(value) < 16:
        return int(value)
    return str(value)


def detail_labels(detail: str) -> dict[str, str | int | float]:
    """Return the parts of ``detail`` as labels, keyed without the ``labels.`` prefix.

    ``name:value`` becomes ``name`` (a repeated name keeps its values comma-separated; the value
    may hold colons of its own, ``grant.add:org_admin:demo``); ``name:`` with nothing after it
    is a field without a value and gives no label; a bare part is a flag and joins ``what``. A
    part whose name is not a plain code is kept as a flag, so nothing of the detail is lost.
    """
    labels: dict[str, str | int | float] = {}
    flags: list[str] = []
    for part in (p.strip() for p in detail.split(",")):
        if not part:
            continue
        name, sep, value = part.partition(":")
        if sep and _LABEL_NAME.fullmatch(name):
            if not value:
                continue
            key = label_name(name)
            labels[key] = f"{labels[key]},{value}" if key in labels else label_value(value)
        else:
            flags.append(part)
    if flags:
        labels[FLAGS] = ",".join(flags)
    return labels


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


_NOT_IN_A_FLAG = frozenset(" =;")


def detail_problem(detail: str) -> str | None:
    """Return why ``detail`` breaks the convention, or ``None`` when it follows it.

    The convention (ADR 0103 point 6): comma-separated parts, each ``name:value`` with a plain
    code as name, or a bare flag without spaces, ``=`` or ``;``. The test suite checks every
    detail it writes against this, so a new writer with a form of its own fails there.
    """
    for part in detail.split(",") if detail else ():
        name, sep, _value = part.partition(":")
        if not part.strip():
            return "empty part"
        if sep and _LABEL_NAME.fullmatch(name):
            continue
        if sep or _NOT_IN_A_FLAG & set(part):
            return f"part {part[:40]!r} is neither name:value nor a flag"
    return None


def to_record(event: AuditEvent, envelope: Envelope) -> dict[str, Value]:
    """Return ``event`` as an ``openplanner.event/3`` record: ``FIELDS`` in order, then labels.

    ``message`` is rendered from the register with the event's own fields and its labels, so it
    carries nothing the record does not already carry.
    """
    labels = detail_labels(event.detail)
    values: dict[str, object] = {
        "action": event.action,
        "outcome": event.outcome.value,
        "reason": event.reason,
        "organisation": event.organisation,
        "group": event.group,
        "actor": event.actor,
        "booking_id": event.booking_id,
        "detail": event.detail,
        **labels,
    }
    fixed: dict[str, Value] = {
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
        "actor.type": event.actor_type.value,
        "actor.role": event.actor_role,
        "trace.id": event.trace_id,
        "service.name": event.service,
        "service.version": envelope.service_version,
        "installation": envelope.installation,
        "message": describe_event(
            event.action,
            values,
            outcome=event.outcome.value,
            reason=event.reason,
            organisation=event.organisation,
            group=event.group,
        ),
        "error.message": event.message,
        "detail": event.detail,
    }
    record = {name: fixed[name] for name in FIELDS}
    record.update({f"{LABELS}{name}": value for name, value in labels.items()})
    return record
