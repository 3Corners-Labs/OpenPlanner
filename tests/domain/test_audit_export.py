"""CSV export of the audit log (ADR 0040, part 3): format, injection guard, limits, audit."""

import csv
import io
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, AuditFilter, Category, Level, Outcome, Source
from app.domain.audit_export import (
    BOM,
    EXPORT_EVENT,
    ExportLimits,
    ExportRefused,
    export_window,
    safe_cell,
    to_csv,
)
from app.domain.authorization import Principal, Role
from app.domain.event_schema import FIELDS, Envelope, to_record
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

ORG_ADMIN = Principal(
    "sub-org", "Olga", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset()
)
LIMITS = ExportLimits(max_rows=3, max_days=31)


def _rows(document: str) -> list[list[str]]:
    assert document.startswith(BOM)
    return list(csv.reader(io.StringIO(document[1:], newline=""), delimiter=";"))


@pytest.mark.parametrize("text", ["=cmd|' /C calc'!A0", "+1", "-1+2", "@SUM(A1)", "\tx", "\rx"])
def test_a_formula_becomes_text(text: str) -> None:
    assert safe_cell(text) == f"'{text}"


def test_plain_values_stay_as_they_are() -> None:
    assert (safe_cell("group.edit"), safe_cell(42), safe_cell(None)) == ("group.edit", "42", "")


def test_csv_is_bom_semicolon_crlf_and_rfc4180_quoted() -> None:
    event = AuditEvent(NOW, "sub;1", "group.edit", Outcome.SUCCESS, detail='name,"x"')
    document = to_csv([to_record(event, Envelope())])

    assert document.startswith(BOM + "schema;event_id;@timestamp;")
    assert "\r\n" in document
    header, row = _rows(document)
    assert tuple(header) == FIELDS
    assert row[FIELDS.index("actor")] == "sub;1"
    assert row[FIELDS.index("detail")] == 'name,"x"'


def test_without_a_period_the_last_max_days_are_exported() -> None:
    assert export_window(None, None, NOW, LIMITS) == (NOW - timedelta(days=31), NOW)


def test_a_period_longer_than_allowed_is_refused() -> None:
    with pytest.raises(ExportRefused) as refused:
        export_window(NOW - timedelta(days=32), NOW, NOW, LIMITS)
    assert refused.value.code == "too_many_days"


def _uow(*events: AuditEvent) -> InMemoryUnitOfWork:
    uow = InMemoryUnitOfWork(State())
    uow.state.audit.extend(events)
    return uow


def _event(offset: int, organisation: str | None = "demo", **changes: object) -> AuditEvent:
    values: dict[str, object] = {
        "at": NOW - timedelta(minutes=offset),
        "actor": "sub-1",
        "action": "group.edit",
        "outcome": Outcome.SUCCESS,
        "organisation": organisation,
    }
    return AuditEvent(**{**values, **changes})  # type: ignore[arg-type]


async def test_the_export_is_the_filter_inside_the_scope_and_is_audited() -> None:
    uow = _uow(
        _event(3),
        _event(2, organisation="andere"),
        _event(1, source=Source.SYSTEM, outcome=Outcome.FAILURE),
    )
    service = AdminService(uow, FixedClock(NOW))

    document, rows = await service.export_audit(
        ORG_ADMIN, AuditFilter(min_level=Level.ERROR), Envelope("1.0", "x"), LIMITS
    )

    assert rows == 1
    (_, row) = _rows(document)
    assert row[FIELDS.index("organisation")] == "demo"
    assert row[FIELDS.index("log.level")] == "error"
    export = uow.state.audit[-1]
    assert (export.action, export.actor, export.detail) == (EXPORT_EVENT, "sub-org", "rows:1")
    assert (export.organisation, export.group, export.outcome) == ("demo", None, Outcome.SUCCESS)


async def test_an_export_over_the_row_limit_is_refused_and_audited() -> None:
    uow = _uow(*(_event(minute) for minute in range(1, 5)))
    service = AdminService(uow, FixedClock(NOW))

    with pytest.raises(ExportRefused) as refused:
        await service.export_audit(ORG_ADMIN, AuditFilter(), Envelope(), LIMITS)

    assert refused.value.code == "too_many_rows"
    export = uow.state.audit[-1]
    assert (export.action, export.outcome, export.reason) == (
        EXPORT_EVENT,
        Outcome.FAILURE,
        "too_many_rows",
    )


def test_the_filter_narrows_by_level_category_period_and_trace() -> None:
    event = _event(10, trace_id="a" * 32, category=Category.SECURITY, outcome=Outcome.DENIED)

    assert AuditFilter(min_level=Level.WARNING).matches(event)
    assert not AuditFilter(min_level=Level.ERROR).matches(event)
    assert AuditFilter(category=Category.SECURITY).matches(event)
    assert not AuditFilter(category=Category.AUDIT).matches(event)
    assert AuditFilter(since=event.at, until=event.at + timedelta(seconds=1)).matches(event)
    assert not AuditFilter(until=event.at).matches(event)
    assert AuditFilter(trace_id="a" * 32).matches(event)
    assert not AuditFilter(trace_id="b" * 32).matches(event)
