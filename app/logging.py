"""Structured JSON logging with PII masking, in the field names of the one event schema.

Every log line, from structlog or the stdlib (uvicorn, SQLAlchemy, ...), passes through
``mask_pii`` before rendering. Masking is a safety net: code must still never pass PII
to a logger (enforced by the semgrep rules in ``.semgrep/``).

Lines use the envelope of ``openplanner.event/1`` (ADR 0040, part 2), so a log platform reads
them without mapping: ``@timestamp`` (RFC 3339 UTC, milliseconds), ``log.level``, ``message``,
``service.name``, ``service.version``, ``installation``, and, inside a request or job,
``trace.id``, ``organisation`` and ``group`` from ``app.domain.event_context``.
"""

import logging
import sys
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.domain.event_context import current
from app.domain.event_schema import Envelope, rfc3339

# The PII columns of Booking (CLAUDE.md, privacy rules).
PII_FIELDS = frozenset({"name", "email", "phone", "note", "guest_join_url", "host_join_url"})
MASK = "***"


def _mask(value: Any) -> Any:
    """Return ``value`` with PII keys masked at any depth of dicts and sequences."""
    if isinstance(value, Mapping):
        return {k: MASK if k in PII_FIELDS else _mask(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_mask(v) for v in value]
    return value


def mask_pii(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: replace the value of every ``PII_FIELDS`` key with ``MASK``."""
    for key, value in event_dict.items():
        event_dict[key] = MASK if key in PII_FIELDS else _mask(value)
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
    out = stream or sys.stdout
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _timestamp,
        _with_context(envelope or Envelope()),
    ]
    finalize: list[Processor] = [
        mask_pii,
        structlog.processors.format_exc_info,
        _schema_names,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=[*shared, *finalize],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
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
