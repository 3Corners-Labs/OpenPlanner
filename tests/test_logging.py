import io
import json
import logging
import re

import pytest
import structlog

from app.domain.event_context import bind_level, set_service, trace_scope
from app.domain.event_schema import Envelope
from app.domain.privacy import ALIASES
from app.domain.privacy import PII_FIELDS as PII_COLUMNS
from app.logging import (
    MASK,
    PII_FIELDS,
    TRACE,
    TRACE_POINTS,
    configure_logging,
    set_log_level,
    tracer,
    warn_loose_secret_files,
)


@pytest.fixture
def stream() -> io.StringIO:
    out = io.StringIO()
    configure_logging("DEBUG", stream=out)
    yield out
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_pii_fields_cover_the_booking_pii_columns() -> None:
    assert {"name", "email", "phone", "note", "guest_join_url", "host_join_url"} <= PII_FIELDS
    # ADR 0055: the split name and the answers to custom fields.
    assert {"first_name", "last_name", "form_answers", "answers"} <= PII_FIELDS
    # ADR 0063: one home for the list; the mask adds only the other names the data travels by.
    assert PII_FIELDS == PII_COLUMNS | ALIASES


def test_structlog_masks_every_pii_field(stream: io.StringIO) -> None:
    values = {field: f"secret-{field}" for field in PII_FIELDS}

    structlog.get_logger().info("booking.created", booking_id=7, **values)

    output = stream.getvalue()
    for value in values.values():
        assert value not in output
    record = _records(stream)[0]
    assert record["booking_id"] == 7
    assert all(record[field] == MASK for field in PII_FIELDS)


def test_masks_pii_nested_in_dicts_and_lists(stream: io.StringIO) -> None:
    structlog.get_logger().warning(
        "payload",
        payload={"booking": {"email": "a@example.org"}, "rows": [{"phone": "0612345678"}]},
    )

    output = stream.getvalue()
    assert "a@example.org" not in output
    assert "0612345678" not in output


def test_masks_pii_bound_to_logger_context(stream: io.StringIO) -> None:
    structlog.get_logger().bind(email="bound@example.org").info("bound")

    assert "bound@example.org" not in stream.getvalue()


def test_stdlib_loggers_are_rendered_as_masked_json(stream: io.StringIO) -> None:
    logging.getLogger("thirdparty").warning("hello", extra={"email": "lib@example.org"})

    record = _records(stream)[0]
    assert record["message"] == "hello"
    assert record["log.level"] == "warning"
    assert "lib@example.org" not in stream.getvalue()


def test_uvicorn_logs_are_json_and_access_log_is_off(stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").info("server started")
    logging.getLogger("uvicorn.access").info('"GET /b/secret-token HTTP/1.1" 200')

    records = _records(stream)
    assert [r["message"] for r in records] == ["server started"]
    assert "secret-token" not in stream.getvalue()


def test_output_is_json_with_utc_timestamp(stream: io.StringIO) -> None:
    structlog.get_logger().info("tick")

    record = _records(stream)[0]
    assert record["message"] == "tick"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", str(record["@timestamp"]))
    assert record["log.level"] == "info"
    assert "event" not in record


def test_respects_log_level() -> None:
    out = io.StringIO()
    configure_logging("WARNING", stream=out)
    try:
        structlog.get_logger().info("quiet")
        assert out.getvalue() == ""
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


def test_lines_carry_the_service_and_the_trace_of_their_request() -> None:
    # ADR 0040, part 2: every line can be filtered per tenant and followed per request.
    out = io.StringIO()
    configure_logging("INFO", stream=out, envelope=Envelope("1.4.0", "gemeente-test"))
    set_service("web")
    try:
        with trace_scope() as trace_id:
            bind_level("gemeente", "burgerzaken")
            structlog.get_logger().info("inside")
            logging.getLogger("thirdparty").warning("library")
            structlog.get_logger().info("own", group="other")
        structlog.get_logger().info("outside")
    finally:
        set_service("")
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    inside, library, own, outside = _records(out)
    for record in (inside, library):
        assert record["trace.id"] == trace_id
        assert (record["organisation"], record["group"]) == ("gemeente", "burgerzaken")
        assert record["service.name"] == "web"
        assert (record["service.version"], record["installation"]) == ("1.4.0", "gemeente-test")
    assert own["group"] == "other"
    assert "trace.id" not in outside
    assert "organisation" not in outside


def test_a_loose_secret_file_is_reported_by_variable_name_only(stream: io.StringIO) -> None:
    warn_loose_secret_files(["SESSION_SECRET_FILE"])

    (record,) = _records(stream)
    assert record["message"] == "settings.secret_file_readable"
    assert record["variable"] == "SESSION_SECRET_FILE"


def test_the_level_can_change_while_running_and_masking_stays() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    structlog.get_logger().debug("hidden", email="ada@example.org")

    set_log_level("DEBUG")
    structlog.get_logger().debug("shown", email="ada@example.org")

    out = stream.getvalue()
    assert "hidden" not in out
    assert "shown" in out
    assert "ada@example.org" not in out
    set_log_level("INFO")


# ADR 0085 part 2: the TRACE level and the fixed trace points.


def _trace_stream(level: str) -> io.StringIO:
    out = io.StringIO()
    configure_logging(level, stream=out)
    return out


def test_trace_is_a_level_below_debug() -> None:
    assert TRACE < logging.DEBUG
    assert logging.getLevelName(TRACE) == "TRACE"
    assert logging.getLevelNamesMapping()["TRACE"] == TRACE


def test_trace_lines_appear_at_trace_level_only() -> None:
    try:
        quiet = _trace_stream("DEBUG")
        tracer.trace("http.request", method="GET", route="/g/{slug}", status=200, ms=3)
        assert quiet.getvalue() == ""

        loud = _trace_stream("TRACE")
        tracer.trace("http.request", method="GET", route="/g/{slug}", status=200, ms=3)
        record = _records(loud)[0]
        assert record["message"] == "http.request"
        assert record["log.level"] == "trace"
        assert record["route"] == "/g/{slug}"
        assert record["status"] == 200
        assert "name" not in record  # the stdlib logger's name does not leak into the line
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


def test_every_trace_point_passes_pii_in_its_fields_masked() -> None:
    try:
        out = _trace_stream("TRACE")
        for point, fields in TRACE_POINTS.items():
            tracer.trace(point, **{field: f"pii-{field}@example.org" for field in fields})
        output = out.getvalue()
        assert "example.org" not in output or all(
            f"pii-{field}@example.org" not in output
            for fields in TRACE_POINTS.values()
            for field in fields
            if field in PII_FIELDS
        )
        assert not PII_FIELDS & {field for fields in TRACE_POINTS.values() for field in fields}
        assert len(_records(out)) == len(TRACE_POINTS)
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


def test_a_trace_point_takes_only_its_registered_fields() -> None:
    with pytest.raises(ValueError, match="unknown trace point"):
        tracer.trace("sql.text")
    with pytest.raises(ValueError, match="not a field of"):
        tracer.trace("http.request", path="/b/secret-token")


def test_the_five_trace_points_of_the_adr_are_registered() -> None:
    assert set(TRACE_POINTS) == {
        "http.request",
        "job.run",
        "job.step",
        "outbound.request",
        "sql.slow",
        "template.render",
    }
    assert "path" not in TRACE_POINTS["http.request"]  # the route template, never the raw path
    assert not {"url", "body", "headers"} & set(TRACE_POINTS["outbound.request"])
    assert "text" not in TRACE_POINTS["sql.slow"]


def test_httpx_request_lines_with_their_urls_are_not_logged(stream: io.StringIO) -> None:
    logging.getLogger("httpx").info('HTTP Request: GET https://h/x?token=secret-token "200"')

    assert stream.getvalue() == ""
