import io
import json
import logging

import pytest
import structlog

from app.logging import MASK, PII_FIELDS, configure_logging


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
    assert record["event"] == "hello"
    assert record["level"] == "warning"
    assert "lib@example.org" not in stream.getvalue()


def test_uvicorn_logs_are_json_and_access_log_is_off(stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").info("server started")
    logging.getLogger("uvicorn.access").info('"GET /b/secret-token HTTP/1.1" 200')

    records = _records(stream)
    assert [r["event"] for r in records] == ["server started"]
    assert "secret-token" not in stream.getvalue()


def test_output_is_json_with_utc_timestamp(stream: io.StringIO) -> None:
    structlog.get_logger().info("tick")

    record = _records(stream)[0]
    assert record["event"] == "tick"
    assert str(record["timestamp"]).endswith("Z")


def test_respects_log_level() -> None:
    out = io.StringIO()
    configure_logging("WARNING", stream=out)
    try:
        structlog.get_logger().info("quiet")
        assert out.getvalue() == ""
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
