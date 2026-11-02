"""Structured JSON logging with PII masking, in the field names of the one event schema.

Every log line, from structlog or the stdlib (uvicorn, SQLAlchemy, ...), passes through
``mask_pii`` before rendering. Masking is a safety net: code must still never pass PII
to a logger (enforced by the semgrep rules in ``.semgrep/``).

Lines use the envelope of ``openplanner.event/2`` (ADR 0040, part 2), so a log platform reads
them without mapping: ``@timestamp`` (RFC 3339 UTC, milliseconds), ``log.level``, ``message``,
``service.name``, ``service.version``, ``installation``, and, inside a request or job,
``trace.id``, ``organisation`` and ``group`` from ``app.domain.event_context``.

``TRACE`` (ADR 0085 part 2) sits below ``DEBUG``. Trace lines come only from the fixed points
in ``TRACE_POINTS``, through ``tracer.trace(point, **fields)``: a point takes its registered
fields and nothing else, so a raw path, a URL, a body or a PII field cannot slip in; the mask
still runs over the line as the safety net. ``docs/logging.md`` lists the points; a test
keeps it in step with the registry.
"""

import logging
import re
import sys
from collections.abc import Callable, Mapping, MutableMapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.domain.event_context import current
from app.domain.event_schema import Envelope, rfc3339
from app.domain.privacy import ALIASES
from app.domain.privacy import PII_FIELDS as PII_COLUMNS

PII_FIELDS = PII_COLUMNS | ALIASES
"""Every personal-data field of ``Booking`` (``app.domain.privacy``, the one home of the
list, ADR 0063) plus the other names the same data travels by (``name``, ``answers``)."""
MASK = "***"

TRACE = 5
"""The level below ``DEBUG`` (ADR 0085 point 8); syslog's numbering leaves it free."""
logging.addLevelName(TRACE, "TRACE")

TRACE_LOGGER = "openplanner.trace"
"""The stdlib logger every trace line goes through, so it shares the root's level and format."""

TRACE_POINTS: Mapping[str, tuple[str, ...]] = {
    "http.request": ("method", "route", "status", "ms"),
    "job.run": ("type", "attempt", "ms"),
    "job.step": ("type", "step", "ms"),
    "outbound.request": ("method", "host", "status", "ms", "error"),
    "sql.slow": ("statement", "table", "ms"),
    "template.render": ("template", "ms"),
}
"""The trace points of ADR 0085 point 9 and the only fields each may carry.

``http.request`` takes the route template, never the raw path (``/b/{token}`` carries the
management token); ``outbound.request`` the host, never a URL, query, header or body;
``sql.slow`` the statement kind and table, never the text.
"""


class Tracer:
    """Writes trace lines for the registered points.

    The registry is the guard: a call with a field the point does not take is a
    ``ValueError`` in the tests, so a PII field cannot be added at a call site. (The semgrep
    rule ``no-pii-in-log-call`` does not match ``trace``; adding it is a ``.semgrep/`` change,
    a decision PR of its own.)
    """

    def trace(self, point: str, **fields: object) -> None:
        """Write one trace line for ``point``, when the level allows it.

        Raises:
            ValueError: An unregistered point or a field the point does not take: a
                programming error, caught by the tests, never by the mask.
        """
        allowed = TRACE_POINTS.get(point)
        if allowed is None:
            raise ValueError(f"unknown trace point: {point}")
        for name in fields:
            if name not in allowed:
                raise ValueError(f"{name} is not a field of trace point {point}")
        logger = logging.getLogger(TRACE_LOGGER)
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, point, extra=fields)


tracer = Tracer()
"""The one tracer; adapters, middleware and the worker call ``tracer.trace``."""


def _mask(value: Any) -> Any:
    """Return ``value`` with PII keys masked at any depth of dicts and sequences."""
    if isinstance(value, Mapping):
        return {k: MASK if k in PII_FIELDS else _mask(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_mask(v) for v in value]
    return value


def mask_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``record`` with every ``PII_FIELDS`` key masked at any depth."""
    return {k: MASK if k in PII_FIELDS else _mask(v) for k, v in record.items()}


def mask_pii(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: replace the value of every ``PII_FIELDS`` key with ``MASK``."""
    for key, value in event_dict.items():
        event_dict[key] = MASK if key in PII_FIELDS else _mask(value)
    return event_dict


_BOUND_PARAMETERS = re.compile(r"\[parameters: [^\n]*")
"""SQLAlchemy's rendering of the values bound to a failed statement, on one line."""


def hide_bound_parameters(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: cut SQLAlchemy's ``[parameters: ...]`` from any text (ASVS V16.2.5).

    The engines are built with ``hide_parameters`` so the text never has them; this is the net
    below that for an exception that comes from some other engine, rendered into a log line.
    """
    for key, value in event_dict.items():
        if isinstance(value, str) and "[parameters: " in value:
            event_dict[key] = _BOUND_PARAMETERS.sub(f"[parameters: {MASK}]", value)
    return event_dict


def _stdlib_extra(
    _logger: WrappedLogger, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Copy ``extra=`` attributes of stdlib records into the event so they get masked."""
    record = event_dict.get("_record")
    if record is not None:
        for key, value in vars(record).items():
            if key not in _IGNORED_RECORD_ATTRS and key not in event_dict:
                event_dict[key] = value
    return event_dict


def _timestamp(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Stamp the line with ``@timestamp`` in RFC 3339 UTC with milliseconds."""
    event_dict["@timestamp"] = rfc3339(datetime.now(UTC))
    return event_dict


def _with_context(envelope: Envelope) -> Processor:
    """Return a processor that adds the service and the current trace, organisation, group.

    A value the call passed itself (``group=...``) is kept.
    """

    def add(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        context = current()
        fields = {
            "service.name": context.service,
            "service.version": envelope.service_version,
            "installation": envelope.installation,
            "trace.id": context.trace_id,
            "organisation": context.organisation,
            "group": context.group,
        }
        for key, value in fields.items():
            if value:
                event_dict.setdefault(key, value)
        return event_dict

    return add


def _schema_names(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Rename structlog's ``event`` and ``level`` to ECS's ``message`` and ``log.level``.

    ``event`` would clash with ECS's ``event.*`` fields on a log platform.
    """
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    if "level" in event_dict:
        event_dict["log.level"] = event_dict.pop("level")
    return event_dict


# Standard LogRecord attributes, plus uvicorn's ANSI-coloured duplicate of the message.
_IGNORED_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "color_message"}
_TRACE_RECORD_ATTRS = frozenset({"taskName"})
"""Attributes Python adds to a record that are noise on a trace line."""


LineTap = Callable[[str], None]
"""Receives every finished log line (JSON, masked) as it is written (ADR 0085 point 3)."""

_TAPS: list[LineTap] = []


def set_line_tap(tap: LineTap | None) -> None:
    """Install the one tap that copies every line to the log destinations; ``None`` removes it.

    The tap runs inside the logging call: it must be quick, must not raise and must never log.
    """
    _TAPS[:] = [tap] if tap is not None else []


def tap_line(line: str) -> None:
    """Offer one finished line to the tap; a failing tap is ignored, never raised."""
    for tap in _TAPS:
        # Logging about a failing tap would recurse into the tap.
        with suppress(Exception):
            tap(line)


class _TapStream:
    """A stream that offers every line written to it to the tap, besides writing it.

    Both the structlog logger and the stdlib handler write to this stream, so the tap sees
    exactly what stdout sees: the same masked line, after the level filter.
    """

    def __init__(self, inner: TextIO) -> None:
        """Wrap ``inner``."""
        self._inner = inner

    def write(self, text: str) -> int:
        """Write ``text`` and offer each non-empty line in it to the tap."""
        written = self._inner.write(text)
        if _TAPS:
            for line in text.splitlines():
                if line:
                    tap_line(line)
        return written

    def flush(self) -> None:
        """Flush the inner stream."""
        self._inner.flush()

    def __getattr__(self, name: str) -> object:
        """Delegate everything else (``isatty``, ``fileno``...) to the inner stream."""
        return getattr(self._inner, name)


_CONFIGURED: list[tuple[TextIO, Envelope | None]] = []
"""The stream and envelope of the last ``configure_logging``, for ``set_log_level``."""


def set_log_level(level: str) -> None:
    """Change the minimum level live, keeping stream and envelope (ADR 0042 §6).

    The PII masking applies at every level. Before ``configure_logging`` it does nothing.
    """
    if _CONFIGURED:
        stream, envelope = _CONFIGURED[-1]
        configure_logging(level, stream, envelope)


def configure_logging(
    level: str, stream: TextIO | None = None, envelope: Envelope | None = None
) -> None:
    """Configure structlog and the stdlib root logger to emit masked JSON lines.

    Safe to call more than once (tests do); each call replaces the previous configuration.

    Args:
        level: Minimum level name, e.g. ``"INFO"``.
        stream: Output stream; defaults to stdout. Tests pass a ``StringIO``.
        envelope: Release and installation name added to every line.
    """
    chosen = stream or sys.stdout
    _CONFIGURED[:] = [(chosen, envelope)]
    out: TextIO = _TapStream(chosen)  # type: ignore[assignment]
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _timestamp,
        _with_context(envelope or Envelope()),
    ]
    finalize: list[Processor] = [
        mask_pii,
        structlog.processors.format_exc_info,
        hide_bound_parameters,
        _schema_names,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=[*shared, *finalize],
        # structlog's filtering logger knows no level below DEBUG; trace lines take the
        # stdlib route (``tracer``), which the root logger's level filters.
        wrapper_class=structlog.make_filtering_bound_logger(
            max(logging.getLevelNamesMapping()[level], logging.DEBUG)
        ),
        logger_factory=structlog.WriteLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(out)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[*shared, _stdlib_extra],
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *finalize],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # uvicorn installs its own plain-text handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Access lines contain request paths, which will carry management tokens (/b/{token}).
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs every outgoing request at INFO with its full URL, query string included; the
    # ``outbound.request`` trace point (ADR 0085) carries method, host and status instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # aiosqlite logs each statement with its bound parameters at DEBUG, which holds PII; the
    # ``sql.slow`` trace point carries statement type and table only.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def warn_loose_secret_files(variables: list[str]) -> None:
    """Log one warning per ``X_FILE`` whose file others may read (ADR 0042 §9); names only."""
    for variable in variables:
        structlog.get_logger().warning("settings.secret_file_readable", variable=variable)
