"""The ambient context of the request or job being handled: trace id, organisation, group.

One standard event schema (ADR 0040, part 2) tags every container log line and every audit
event with the trace id of the request or job that caused it, and with the organisation and
group it concerns, so one chain can be followed and each tenant's lines can be filtered. The
context lives in ``contextvars``, so concurrent requests never see each other's values:

    request / job ─► trace_scope()        new trace id, no level yet
                  ─► bind_level(org, grp)  once the route or job knows its target
                  ─► log line, AuditEvent  read current() and carry both

The service (``web`` or ``worker``) is set once per process. This module is plain Python so
the domain can stamp events without knowing about logging or HTTP.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")
_ORGANISATION: ContextVar[str | None] = ContextVar("organisation", default=None)
_GROUP: ContextVar[str | None] = ContextVar("group", default=None)
_service = ""

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
"""Shape of a trace id: 32 lower-case hex digits, as an OpenTelemetry trace id."""


@dataclass(frozen=True)
class EventContext:
    """What the current request or job is about.

    Attributes:
        trace_id: Id shared by every line and event of one request or job; empty outside one.
        organisation: Slug of the organisation concerned, once known.
        group: Slug of the group concerned, once known.
        service: ``web`` or ``worker``; empty until the process sets it.
    """

    trace_id: str
    organisation: str | None
    group: str | None
    service: str


def new_id() -> str:
    """Return a fresh random id of 32 hex digits (event ids and trace ids)."""
    return uuid4().hex


def set_service(name: str) -> None:
    """Name this process (``web`` or ``worker``); called once at startup."""
    global _service
    _service = name


@contextmanager
def trace_scope() -> Iterator[str]:
    """Start a new trace with no level for the duration of the block; yield its id.

    The previous values are restored afterwards, so a scope never leaks into the next request
    or job that runs in the same task.
    """
    trace_id = new_id()
    tokens = (_TRACE_ID.set(trace_id), _ORGANISATION.set(None), _GROUP.set(None))
    try:
        yield trace_id
    finally:
        _GROUP.reset(tokens[2])
        _ORGANISATION.reset(tokens[1])
        _TRACE_ID.reset(tokens[0])


def bind_level(organisation: str | None, group: str | None) -> None:
    """Record the organisation and group the current request or job is about."""
    _ORGANISATION.set(organisation)
    _GROUP.set(group)


def current() -> EventContext:
    """Return the context of the current request or job."""
    return EventContext(_TRACE_ID.get(), _ORGANISATION.get(), _GROUP.get(), _service)


def current_trace_id() -> str:
    """Return the trace id of the current request or job; empty outside one."""
    return _TRACE_ID.get()


def current_service() -> str:
    """Return the name of this process; empty until it is set."""
    return _service
