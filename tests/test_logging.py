import io
import json
import logging
import re

import pytest
import structlog

from app.domain.event_context import bind_level, set_service, trace_scope
from app.domain.event_schema import Envelope
from app.logging import MASK, PII_FIELDS, configure_logging, warn_loose_secret_files


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
