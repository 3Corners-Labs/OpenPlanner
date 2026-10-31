"""Capture the trace lines of ``app.logging.tracer`` in a test (ADR 0085 part 2)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.logging import TRACE, TRACE_LOGGER


@dataclass
class TraceLine:
    """One captured trace point with its fields."""

    point: str
    fields: dict[str, object] = field(default_factory=dict)


class _Capture(logging.Handler):
    def __init__(self, lines: list[TraceLine]) -> None:
        super().__init__(TRACE)
        self._lines = lines
        self._standard = frozenset(vars(logging.makeLogRecord({}))) | {"message"}

    def emit(self, record: logging.LogRecord) -> None:
        fields = {k: v for k, v in vars(record).items() if k not in self._standard}
        self._lines.append(TraceLine(record.getMessage(), fields))


@contextmanager
def captured_trace() -> Iterator[list[TraceLine]]:
    """Collect every trace line written while the block runs, whatever the log level."""
    lines: list[TraceLine] = []
    logger = logging.getLogger(TRACE_LOGGER)
    handler = _Capture(lines)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(TRACE)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
