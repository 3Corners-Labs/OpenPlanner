"""Audit events on the log stream, for the log platform (ADR 0034).

After a transaction commits, every audit, system and security event it wrote is written once
to stdout as one JSON line: an ``openplanner.event/2`` record (ADR 0040) plus
``log_type: "audit"``, which the platform routes to its own index. Diagnostic events stay in
the portal. The line does not pass the ``LOG_LEVEL`` filter, so a quiet log level never drops
the audit trail; it does pass the PII mask, as a safety net on top of a schema that holds ids
and codes only. Shipping itself is the host's job (Docker's ``splunk`` driver, a Universal
Forwarder, Fluent Bit): the application has no credential for the platform.

    commit ─► SqlUnitOfWork ─► StdoutEventSink.emit(events) ─► {"log_type":"audit", ...}
"""

import json
import sys
from collections.abc import Sequence
from typing import TextIO

import structlog

from app.domain.audit import AuditEvent, Category
from app.domain.event_schema import Envelope, to_record
from app.logging import mask_record

log = structlog.get_logger()

LOG_TYPE = "audit"
"""Marker the log platform routes on."""

SHIPPED = frozenset({Category.AUDIT, Category.SYSTEM, Category.SECURITY})
"""Categories that leave the installation; diagnostic events never do (ADR 0034)."""


class StdoutEventSink:
    """Writes each shipped event as one JSON line.

    Args:
        envelope: Release and installation name for every record.
        stream: Where to write; ``None`` means the current ``sys.stdout``.
    """

    def __init__(self, envelope: Envelope, stream: TextIO | None = None) -> None:
        """Store the envelope and the stream."""
        self._envelope = envelope
        self._stream = stream

    def emit(self, events: Sequence[AuditEvent]) -> None:
        """Write the shipped events; a failing stream is logged, never raised."""
        out = self._stream or sys.stdout
        try:
            for event in events:
                if event.event_category in SHIPPED:
                    record = mask_record({**to_record(event, self._envelope), "log_type": LOG_TYPE})
                    out.write(json.dumps(record, separators=(",", ":")) + "\n")
            out.flush()
        except Exception as exc:  # after commit nothing may fail the request; the table has it
            log.error("audit.emit_failed", error=type(exc).__name__)
