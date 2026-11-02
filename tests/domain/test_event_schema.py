import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.audit import (
    ActorType,
    AuditEvent,
    Category,
    Level,
    Outcome,
    Source,
    actor_type,
)
from app.domain.event_context import bind_actor_role, bind_level, set_service, trace_scope
from app.domain.event_schema import (
    FIELDS,
    SCHEMA,
    Envelope,
    detail_labels,
    detail_problem,
    rfc3339,
    to_record,
)
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
        message="timezone and offset do not match",
    )

    record = to_record(event, Envelope("1.4.0", "acme-test"))

    assert tuple(record) == (*FIELDS, "labels.attempt")
    assert record == {
        "@timestamp": "2026-10-27T14:03:12.345Z",
        "log.level": "warning",
        "event.category": "system",
        "event.action": "group.edit",
        "message": "Admin action under group.edit: retry. Reason: timeout. Organisation org, "
        "group grp.",
        "outcome": "retry",
        "event.outcome": "failure",
        "reason": "timeout",
        "organisation": "org",
        "group": "grp",
        "booking_id": 42,
        "actor": "sub-1",
        "actor.type": "user",
        "actor.role": "",
        "error.message": "timezone and offset do not match",
        "detail": "attempt:2",
        "trace.id": "a" * 32,
        "event_id": event.event_id,
        "service.name": "worker",
        "service.version": "1.4.0",
        "installation": "acme-test",
        "schema": SCHEMA,
        "labels.attempt": 2,
    }


def test_the_schema_is_version_3_and_reads_when_level_and_what_first() -> None:
    assert SCHEMA == "openplanner.event/3"
    assert FIELDS[:5] == ("@timestamp", "log.level", "event.category", "event.action", "message")


def test_a_refused_change_does_not_read_as_a_change_that_happened() -> None:
    event = _event(
        action="idp.updated", outcome=Outcome.DENIED, reason="out_of_scope", organisation="gem"
    )

    message = to_record(event, Envelope())["message"]

    assert message == (
        "An identity provider was changed. Outcome: denied (out_of_scope). Organisation gem."
    )


def test_a_success_names_no_outcome_and_a_global_event_no_organisation() -> None:
    assert to_record(_event(action="idp.updated"), Envelope())["message"] == (
        "An identity provider was changed."
    )


def test_the_sentence_is_filled_from_the_detail() -> None:
    event = _event(
        actor="host-updater",
        action="system.update",
        source=Source.SYSTEM,
        detail="update.succeeded,from:0.4.0-rc.131,to:0.4.0-rc.134",
    )

    record = to_record(event, Envelope())

    assert record["message"] == (
        "Update step update.succeeded from 0.4.0-rc.131 to 0.4.0-rc.134: success."
    )
    assert (record["labels.what"], record["labels.from"], record["labels.to"]) == (
        "update.succeeded",
        "0.4.0-rc.131",
        "0.4.0-rc.134",
    )
    assert record["actor.type"] == "host"


def test_a_missing_field_reads_as_a_dash_and_never_fails() -> None:
    event = _event(action="system.version", source=Source.SYSTEM, detail="version.changed")

    assert to_record(event, Envelope())["message"] == (
        "The running version changed from - to - (commit -)."
    )


@pytest.mark.parametrize(
    ("detail", "labels"),
    [
        ("", {}),
        ("horizon_days,name", {"what": "horizon_days,name"}),
        ("free_percent:6,database_mb:120", {"free_percent": 6, "database_mb": 120}),
        ("grant.add:org_admin:demo", {"grant_add": "org_admin:demo"}),
        ("legacy_only:a,legacy_only:b", {"legacy_only": "a,b"}),
        ("tracking:,version:0.4.0", {"version": "0.4.0"}),
        ("Not A Name:x", {"what": "Not A Name:x"}),
    ],
)
def test_detail_parts_become_labels(detail: str, labels: dict[str, object]) -> None:
    assert detail_labels(detail) == labels


@pytest.mark.parametrize(
    "detail",
    ["", "horizon_days,name", "role.add:org_admin,grant.add:org_admin:demo", "issuer:R 11"],
)
def test_details_that_follow_the_convention(detail: str) -> None:
    assert detail_problem(detail) is None


@pytest.mark.parametrize(
    "detail",
    ["sessions=2", "secrets.rekey resealed=1", "a;b", "a,,b", "Not A Name:x"],
)
def test_details_that_break_the_convention(detail: str) -> None:
    assert detail_problem(detail) is not None


@pytest.mark.parametrize(
    ("actor", "kind"),
    [
        ("system", ActorType.SYSTEM),
        ("anonymous", ActorType.ANONYMOUS),
        ("", ActorType.ANONYMOUS),
        ("host", ActorType.HOST),
        ("host-updater", ActorType.HOST),
        ("system:cli", ActorType.HOST),
        ("opc_abcdefghijklmnop", ActorType.API),
        ("8c1f2d", ActorType.USER),
        ("hostess", ActorType.USER),
    ],
)
def test_the_actor_type_follows_the_shape_of_the_actor(actor: str, kind: ActorType) -> None:
    assert actor_type(actor) is kind


def test_an_admins_event_carries_the_role_bound_to_the_request_and_a_system_event_does_not() -> (
    None
):
    with trace_scope():
        bind_actor_role("org_admin")
        admin, system = _event(), _event(actor="system", source=Source.SYSTEM)
    outside = _event()

    assert to_record(admin, Envelope())["actor.role"] == "org_admin"
    assert system.actor_role == ""
    assert outside.actor_role == ""


def test_skipped_is_unknown_in_ecs() -> None:
    assert to_record(_event(outcome=Outcome.SKIPPED), Envelope())["event.outcome"] == "unknown"


def test_record_has_no_pii_field() -> None:
    assert not PII_FIELDS & set(FIELDS)
