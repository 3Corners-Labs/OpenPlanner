"""Audit events on the log stream after commit (ADR 0034, AU-1)."""

import io
import json

from app.adapters.event_sink import StdoutEventSink
from app.domain.audit import AuditEvent, Category, Level, Outcome, Source
from app.domain.event_schema import FIELDS, Envelope
from app.logging import PII_FIELDS
from tests.support.domain import NOW

ENVELOPE = Envelope("1.4.0", "gemeente-test")


def _event(action: str = "group.edit", **changes: object) -> AuditEvent:
    return AuditEvent(NOW, "sub-1", action, Outcome.SUCCESS, **changes)  # type: ignore[arg-type]


def _lines(out: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_a_shipped_event_is_one_line_with_exactly_the_schema_fields() -> None:
    out = io.StringIO()
    event = _event(organisation="demo", group="aangifte")

    StdoutEventSink(ENVELOPE, out).emit([event])

    (line,) = _lines(out)
    assert set(line) == {*FIELDS, "log_type"}
    assert line["log_type"] == "audit"
    assert (line["event_id"], line["actor"], line["group"]) == (event.event_id, "sub-1", "aangifte")
    assert line["installation"] == "gemeente-test"
    assert not PII_FIELDS & set(line)


def test_diagnostic_events_never_leave_the_installation() -> None:
    out = io.StringIO()
    events = [
        _event("admin.login"),
        _event("job.purge_pii", source=Source.SYSTEM),
        _event("webhook.rejected", source=Source.SYSTEM, category=Category.SECURITY),
        _event("debug.slots", category=Category.DIAGNOSTIC, level=Level.DEBUG),
    ]

    StdoutEventSink(ENVELOPE, out).emit(events)

    assert [line["event.category"] for line in _lines(out)] == ["audit", "system", "security"]


def test_a_broken_stream_is_logged_not_raised() -> None:
    out = io.StringIO()
    out.close()

    StdoutEventSink(ENVELOPE, out).emit([_event()])  # must not raise


# ADR 0085 point 3: the log destinations get the audit line, and diagnostic events if they opt in.


def test_the_tap_gets_the_audit_line_and_diagnostic_events_only_the_tap() -> None:
    from app.logging import set_line_tap  # noqa: PLC0415

    tapped: list[str] = []
    out = io.StringIO()
    set_line_tap(tapped.append)
    try:
        StdoutEventSink(ENVELOPE, out).emit(
            [_event("admin.login"), _event("job.run", category=Category.DIAGNOSTIC)]
        )
    finally:
        set_line_tap(None)

    assert [json.loads(line)["event.category"] for line in tapped] == ["audit", "diagnostic"]
    assert [line["event.category"] for line in _lines(out)] == ["audit"]  # stdout: no diagnostic
