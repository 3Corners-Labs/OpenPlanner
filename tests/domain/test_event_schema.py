import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.audit import (
    AuditEvent,
    Category,
    Level,
    Outcome,
    Source,
)
from app.domain.event_context import bind_level, set_service, trace_scope
from app.domain.event_schema import FIELDS, SCHEMA, Envelope, rfc3339, to_record
from app.logging import PII_FIELDS

AT = datetime(2026, 10, 27, 14, 3, 12, 345678, tzinfo=UTC)


def _event(**changes: object) -> AuditEvent:
    values: dict[str, object] = {
        "at": AT,
        "actor": "sub-1",
        "action": "group.edit",
        "outcome": Outcome.SUCCESS,
    }
    values.update(changes)
    return AuditEvent(**values)  # type: ignore[arg-type]


def test_rfc3339_is_utc_with_milliseconds_and_z() -> None:
    amsterdam = timezone(timedelta(hours=1))

    assert rfc3339(AT) == "2026-10-27T14:03:12.345Z"
    assert rfc3339(datetime(2026, 10, 27, 15, 0, tzinfo=amsterdam)) == "2026-10-27T14:00:00.000Z"


def test_rfc3339_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="naive"):
        rfc3339(datetime(2026, 10, 27))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("outcome", "level"),
    [
        (Outcome.SUCCESS, Level.INFO),
        (Outcome.SKIPPED, Level.INFO),
        (Outcome.RETRY, Level.WARNING),
        (Outcome.DENIED, Level.WARNING),
        (Outcome.FAILURE, Level.ERROR),
    ],
)
def test_level_follows_the_outcome(outcome: Outcome, level: Level) -> None:
    assert _event(outcome=outcome).log_level is level


def test_an_explicit_level_wins() -> None:
    assert _event(level=Level.DEBUG).log_level is Level.DEBUG


def test_category_follows_the_source_unless_set() -> None:
    assert _event().event_category is Category.AUDIT
    assert _event(source=Source.SYSTEM).event_category is Category.SYSTEM
    assert _event(source=Source.SYSTEM, category=Category.SECURITY).event_category is (
        Category.SECURITY
    )


def test_every_event_gets_its_own_id_which_equality_ignores() -> None:
    first, second = _event(), _event()

    assert re.fullmatch(r"[0-9a-f]{32}", first.event_id)
    assert first.event_id != second.event_id
    assert first == second


def test_an_event_carries_the_trace_and_service_of_its_context() -> None:
    set_service("worker")
    try:
        with trace_scope() as trace_id:
            bind_level("org", "grp")
            event = _event()
    finally:
        set_service("")

    assert event.trace_id == trace_id
    assert event.service == "worker"


def test_record_has_every_field_in_order() -> None:
    event = _event(
        source=Source.SYSTEM,
        outcome=Outcome.RETRY,
        reason="timeout",
        organisation="org",
        group="grp",
        booking_id=42,
        detail="attempt:2",
        trace_id="a" * 32,
        service="worker",
    )

    record = to_record(event, Envelope("1.4.0", "gemeente-test"))

    assert tuple(record) == FIELDS
    assert record == {
        "schema": SCHEMA,
        "event_id": event.event_id,
        "@timestamp": "2026-10-27T14:03:12.345Z",
        "log.level": "warning",
        "event.category": "system",
        "event.action": "group.edit",
        "event.outcome": "failure",
        "outcome": "retry",
        "reason": "timeout",
        "organisation": "org",
        "group": "grp",
        "booking_id": 42,
        "actor": "sub-1",
        "trace.id": "a" * 32,
        "service.name": "worker",
        "service.version": "1.4.0",
        "installation": "gemeente-test",
        "detail": "attempt:2",
    }


def test_skipped_is_unknown_in_ecs() -> None:
    assert to_record(_event(outcome=Outcome.SKIPPED), Envelope())["event.outcome"] == "unknown"


def test_record_has_no_pii_field() -> None:
    assert not PII_FIELDS & set(FIELDS)
