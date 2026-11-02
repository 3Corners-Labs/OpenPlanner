"""Every log call and trace point in ``app/`` is registered (ADR 0103 point 1).

A static scan of the source: a call ``<logger>.<level>("action", field=...)`` or
``<logger>.log(level, "action", ...)`` must name a registered action, at a level its entry
lists, with fields its entry declares; ``tracer.trace("point", ...)`` must name a trace point.
And the other way round: every listed entry that is written as a log line must still be
written somewhere, so the register holds no dead actions.

Audit, system and security events build their action in many ways (a permission, a constant,
``job.<type>``); they are checked while the test suite runs (``tests/conftest.py``).
"""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.log_destinations import TEST_ACTION
from app.domain.log_events import BY_OUTCOME, COMMON_FIELDS, ENTRIES, EVENTS

APP = Path(__file__).resolve().parents[1] / "app"

LOGGERS = frozenset({"log", "logger", "structlog.get_logger()"})
"""Receivers that are structlog loggers; ``self._diagnostics.debug`` is an audit event."""

DYNAMIC = frozenset({"app/logging.py"})
"""Files that may log a variable action: the ``Tracer``, which checks the register itself."""

LEVEL_METHODS = {
    "debug": "debug",
    "info": "info",
    "warning": "warning",
    "error": "error",
    "exception": "error",
    "critical": "critical",
}


@dataclass(frozen=True)
class Call:
    """One log or trace call found in the source."""

    where: str
    action: str | None
    level: str | None
    fields: frozenset[str]


def _calls() -> Iterator[Call]:
    """Yield every log and trace call with a literal action in ``app/``."""
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver, method = ast.unparse(node.func.value), node.func.attr
            args = node.args
            if receiver == "tracer" and method == "trace":
                level, action_arg = "trace", args[0] if args else None
            elif receiver in LOGGERS and method in LEVEL_METHODS:
                level, action_arg = LEVEL_METHODS[method], args[0] if args else None
            elif receiver in LOGGERS and method == "log":
                level, action_arg = None, args[1] if len(args) > 1 else None
            else:
                continue
            where = f"{path.relative_to(APP.parent)}:{node.lineno}"
            literal = isinstance(action_arg, ast.Constant) and isinstance(action_arg.value, str)
            action = action_arg.value if literal else None  # type: ignore[union-attr]
            fields = frozenset(k.arg for k in node.keywords if k.arg is not None)
            yield Call(where, action, level, fields)


FOUND = list(_calls())
CALLS = [call for call in FOUND if call.action is not None]


def test_the_action_of_a_log_call_is_a_literal() -> None:
    dynamic = [c.where for c in FOUND if c.action is None and c.where.split(":")[0] not in DYNAMIC]

    assert dynamic == [], "name the action literally, so the register can be checked"


def test_the_scan_finds_the_log_calls() -> None:
    assert len(CALLS) > 100


@pytest.mark.parametrize("call", CALLS, ids=lambda c: f"{c.action}@{c.where}")
def test_a_log_call_is_registered(call: Call) -> None:
    assert call.action is not None
    entry = EVENTS.get(call.action)

    assert entry is not None, (
        f"{call.where}: `{call.action}` is not in app/domain/log_events.py; register it"
    )
    if call.level is not None:
        assert call.level in entry.levels, (
            f"{call.where}: `{call.action}` is logged at {call.level}, the register lists "
            f"{entry.levels}"
        )
    allowed = set(entry.fields) if entry.is_trace_point else {*entry.fields, *COMMON_FIELDS}
    assert call.fields <= allowed, (
        f"{call.where}: `{call.action}` passes {sorted(call.fields - allowed)}, which its "
        "entry does not declare"
    )


BUILT_LINES = {TEST_ACTION}
"""Actions whose line is built, not logged: *Send test line* (``log_shipping.probe_line``)."""


def test_every_listed_log_line_is_still_written() -> None:
    written = {call.action for call in CALLS} | BUILT_LINES
    logged = [e.action for e in ENTRIES if set(e.levels) - {BY_OUTCOME}]

    assert [action for action in logged if action not in written] == []
