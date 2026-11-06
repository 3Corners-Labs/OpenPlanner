"""Fake of the ``EventSink`` port (ADR 0034): keeps every batch of shipped audit events."""

from collections.abc import Sequence

from app.domain.audit import AuditEvent


class RecordingSink:
    """``EventSink`` fake: appends each batch it is handed to ``batches``, in order."""

    def __init__(self) -> None:
        self.batches: list[list[AuditEvent]] = []

    def emit(self, events: Sequence[AuditEvent]) -> None:
        self.batches.append(list(events))
