"""Structured JSON logging with PII masking, in the field names of the one event schema.

Every log line, from structlog or the stdlib (uvicorn, SQLAlchemy, ...), passes through
``mask_pii`` before rendering. Masking is a safety net: code must still never pass PII
to a logger (enforced by the semgrep rules in ``.semgrep/``).

Lines use the envelope of ``openplanner.event/3`` (ADR 0040 part 2, ADR 0103), so a log
platform reads them without mapping: ``schema``, ``@timestamp`` (RFC 3339 UTC, milliseconds),
``log.level``, ``event.action``, ``event.category``, ``message``, ``service.name``,
``service.version``, ``installation``, and, inside a request or job, ``trace.id``,
``organisation``, ``group`` and ``actor.role`` from ``app.domain.event_context``.

A log call names its action (``log.warning("storage.degraded", free_percent=6)``); ``describe``
turns that into the line: the code goes to ``event.action``, the register's category to
``event.category``, the call's own fields to ``labels.<name>``, and ``message`` becomes the
register's sentence with those fields filled in ("Disk space is low: 6% free ..."). It runs after
the PII mask, so the sentence carries only masked values. A line of a library's own logger is
``library.<logger>`` in category ``operations`` and keeps its own text (ADR 0103 point 8).

``LOG_LEVEL`` governs our own loggers only (``OWN_LOGGERS``), ``uvicorn.error`` down to INFO.
Every other stdlib logger stays at WARNING at any level, TRACE included, so a library's DEBUG
lines (bound SQL values, response headers) never reach the stream, the tap or log shipping
(ASVS V16.2.5). A library line that names the client address and the raw path in its own text
(``REQUEST_LINES``: slowapi's rate-limit hit, uvicorn's WebSocket request) is dropped at any
level.

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

from app.domain.audit import actor_type
from app.domain.event_context import current
from app.domain.event_schema import LABELS, SCHEMA, Envelope, label_name, label_value, rfc3339
from app.domain.log_events import (
    COMMON_FIELDS,
    LIBRARY_PREFIX,
    LogCategory,
    describe,
    lookup,
    trace_points,
)
from app.domain.privacy import ALIASES
from app.domain.privacy import PII_FIELDS as PII_COLUMNS

STAFF_FIELDS = frozenset({"username", "screen_name"})
"""Personal data of staff that a log line could carry: the user name of the bundled provider
holds the person's name (ADR 0106 point 11), and so does the screen name the participant of a
call sees (ADR 0123 step 5c). Their e-mail address is masked as ``email``."""
PII_FIELDS = PII_COLUMNS | ALIASES | STAFF_FIELDS
"""Every personal-data field of ``Booking`` (``app.domain.privacy``, the one home of the
list, ADR 0063), the other names the same data travels by (``name``, ``answers``), and the
staff user name."""
MASK = "***"

TRACE = 5
"""The level below ``DEBUG`` (ADR 0085 point 8); syslog's numbering leaves it free."""
logging.addLevelName(TRACE, "TRACE")

TRACE_LOGGER = "openplanner.trace"
"""The stdlib logger every trace line goes through, so it shares the root's level and format."""

TRACE_POINTS: Mapping[str, tuple[str, ...]] = trace_points()
"""The trace points of ADR 0085 point 9 and the only fields each may carry, from the register
of actions (``app.domain.log_events``, ADR 0103).

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
            "actor.role": context.actor_role,
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


LOGGER = "log.logger"
"""ECS field with the name of the stdlib logger a line came from."""

_KEPT = frozenset(COMMON_FIELDS) | {"exception", "stack", LOGGER}
"""Fields of a line that stay where they are: the envelope's own and an exception's text."""


def _logger_name(
    _logger: WrappedLogger, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Note the stdlib logger of a foreign record as ``log.logger``."""
    record = event_dict.get("_record")
    if record is not None:
        event_dict.setdefault(LOGGER, record.name)
    return event_dict


def describe_line(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: give the line the action, category, labels and sentence (ADR 0103).

    Runs after ``_schema_names``, so the call's action is in ``message``.
    """
    action = str(event_dict.get("message", ""))
    entry = lookup(action)
    logger = event_dict.get(LOGGER)
    event_dict["schema"] = SCHEMA
    if entry is None:
        if logger is not None and logger != TRACE_LOGGER:
            event_dict["event.action"] = f"{LIBRARY_PREFIX}{logger}"
        else:
            event_dict["event.action"] = action
        event_dict["event.category"] = LogCategory.OPERATIONS.value
        return _in_reading_order(event_dict)
    event_dict["event.action"] = action
    event_dict["event.category"] = entry.category.value
    values: dict[str, object] = {}
    for name in [k for k in event_dict if k not in _KEPT and not _is_envelope(k)]:
        value = event_dict.pop(name)
        values[name] = value
        event_dict[f"{LABELS}{label_name(name)}"] = label_value(value)
    values.update({k: event_dict[k] for k in COMMON_FIELDS if k in event_dict})
    event_dict["message"] = describe(action, values)
    if "actor" in event_dict:
        event_dict["actor.type"] = actor_type(str(event_dict["actor"])).value
    return _in_reading_order(event_dict)


READING_ORDER = ("@timestamp", "log.level", "event.category", "event.action", "message")
"""The fields a person reads first; a raw line starts with them, the rest follows."""


def _in_reading_order(event_dict: EventDict) -> EventDict:
    """Return the line with ``READING_ORDER`` first, then every other field as it came."""
    ordered: EventDict = {k: event_dict[k] for k in READING_ORDER if k in event_dict}
    ordered.update(event_dict)
    return ordered


_ENVELOPE = frozenset(
    {
        "schema",
        "message",
        "@timestamp",
        "log.level",
        "event.action",
        "event.category",
        "event.outcome",
        "service.name",
        "service.version",
        "installation",
        "trace.id",
        "actor.role",
        "actor.type",
        "error.message",
        "log_type",
    }
)
"""The envelope's fields: never labels."""


def _is_envelope(name: str) -> bool:
    """Return whether ``name`` is an envelope field or already a label."""
    return name in _ENVELOPE or name.startswith(LABELS)


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


OWN_LOGGERS: Mapping[str, int] = {"openplanner": TRACE, "uvicorn.error": logging.INFO}
"""The stdlib loggers that follow ``LOG_LEVEL`` (and their children), each down to its floor.

Ours, including the trace logger ``openplanner.trace``, follow it all the way down. uvicorn's
start-up and error lines follow it down to INFO only: below that, uvicorn writes the address of
every connection on ``uvicorn.error`` (TRACE), and it hands that same logger to the websockets
library, which writes the request line and every header of a WebSocket request, cookies
included, at DEBUG. structlog loggers do not go through the stdlib and follow the level through
their own filter. A fixed protection (ASVS V16.2.5), not a setting."""
FOREIGN_LEVEL = logging.WARNING
"""The lowest level any other logger gets through, whatever ``LOG_LEVEL`` says.

A fixed protection (ASVS V16.2.5), not a setting: libraries log at DEBUG what they handle
(bound SQL values, response headers) and a log line can leave the host through log shipping.
"""


def _floor(name: str) -> int:
    """Return the lowest level the stdlib logger ``name`` gets through (``OWN_LOGGERS``)."""
    for own, floor in OWN_LOGGERS.items():
        if name == own or name.startswith(f"{own}."):
            return floor
    return FOREIGN_LEVEL


class _ForeignLevelFilter(logging.Filter):
    """Drop a record below the floor of its logger: ``FOREIGN_LEVEL`` for one that is not ours.

    The root logger's level only decides for loggers without a level of their own; this filter
    on the handler also holds when a library sets its own logger to DEBUG, or when
    ``set_log_level`` changes the level while running. The tap sits behind it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether the record may be written."""
        return record.levelno >= _floor(record.name)


REQUEST_LINES: Mapping[str, tuple[str, ...]] = {
    "slowapi": ("ratelimit ",),
    "uvicorn.error": ('%s - "WebSocket ',),
}
"""Library lines that carry the request in their own text: never written, at any level.

Keyed by logger, then by the start of the line's message template (``record.msg``, before the
values go in). slowapi writes ``ratelimit 2 per 1 minute (<client address>) exceeded at
endpoint: <path>`` at WARNING on every rate-limit hit; the 429 handlers record the hit as the
security event ``request.rate_limited`` with a pseudonym instead (ADR 0053). uvicorn writes
``<client address> - "WebSocket <path>?<query>" 403`` at INFO for a WebSocket request, which this
application has no routes for. The path is the raw one: ``/b/{token}`` carries the management
token (ADR 0011). A library line keeps its own text (ADR 0103 point 8), so the PII mask cannot
reach into it; the line goes instead. A fixed protection, not a setting.
"""


class _RequestLineFilter(logging.Filter):
    """Drop a library line of ``REQUEST_LINES``, which names the client address and raw path.

    On the handler, like ``_ForeignLevelFilter``, so the stream, the tap and log shipping never
    see the line, whatever level the library logs it at.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether the record may be written."""
        starts = REQUEST_LINES.get(record.name)
        return not (starts and isinstance(record.msg, str) and record.msg.startswith(starts))


_CONFIGURED: list[tuple[TextIO, Envelope | None]] = []
"""The stream and envelope of the last ``configure_logging``, for ``set_log_level``."""


def set_log_level(level: str) -> None:
    """Change the minimum level live, keeping stream and envelope (ADR 0042 §6).

    The PII masking applies at every level, and the level applies to our own loggers only.
    Before ``configure_logging`` it does nothing.
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
        level: Minimum level name, e.g. ``"INFO"``, for our own loggers (``OWN_LOGGERS``).
        stream: Output stream; defaults to stdout. Tests pass a ``StringIO``.
        envelope: Release and installation name added to every line.
    """
    chosen = stream or sys.stdout
    _CONFIGURED[:] = [(chosen, envelope)]
    numeric = logging.getLevelNamesMapping()[level]
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
        describe_line,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=[*shared, *finalize],
        # structlog's filtering logger knows no level below DEBUG; trace lines take the
        # stdlib route (``tracer``), which the root logger's level filters.
        wrapper_class=structlog.make_filtering_bound_logger(max(numeric, logging.DEBUG)),
        logger_factory=structlog.WriteLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(out)
    handler.addFilter(_ForeignLevelFilter())
    handler.addFilter(_RequestLineFilter())
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[*shared, _logger_name, _stdlib_extra],
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *finalize],
        )
    )
    # The level is for our own loggers only, each down to its floor; the root, and so every
    # other library, stays at WARNING (V16.2.5). An allowlist: a library added tomorrow is
    # covered without a line here. uvicorn checks its logger's own level before it writes a
    # connection's address, and websockets before it writes headers, so the floor on the
    # logger keeps those lines from being made at all; the filter on the handler holds anyway.
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(FOREIGN_LEVEL)
    for own, floor in OWN_LOGGERS.items():
        logging.getLogger(own).setLevel(max(numeric, floor))

    # uvicorn installs its own plain-text handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Access lines contain request paths, which will carry management tokens (/b/{token}).
    logging.getLogger("uvicorn.access").disabled = True


def warn_loose_secret_files(variables: list[str]) -> None:
    """Log one warning per ``X_FILE`` whose file others may read (ADR 0042 §9); names only."""
    for variable in variables:
        structlog.get_logger().warning("settings.secret_file_readable", variable=variable)
