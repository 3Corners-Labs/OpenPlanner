import asyncio
import base64
import io
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest
import structlog
import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.adapters.db.engine import create_engine
from app.domain.event_context import bind_actor_role, bind_level, set_service, trace_scope
from app.domain.event_schema import SCHEMA, Envelope
from app.domain.privacy import ALIASES
from app.domain.privacy import PII_FIELDS as PII_COLUMNS
from app.logging import (
    MASK,
    PII_FIELDS,
    TRACE,
    TRACE_POINTS,
    configure_logging,
    set_line_tap,
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
    assert PII_FIELDS == PII_COLUMNS | ALIASES | {"username"}


def test_the_staff_user_name_is_masked() -> None:
    """ADR 0106 point 11: the bundled provider's user name holds the person's name."""
    assert "username" in PII_FIELDS


def test_the_staff_address_is_masked_in_a_log_line(stream: io.StringIO) -> None:
    """ADR 0117 step 10b: the address of a staff member never reaches a log in clear."""
    from app.domain.entitlements import LoginClaims

    claims = LoginClaims("u1", "Olga", email="olga@voorbeeld.nl")

    structlog.get_logger("test").info("login", email=claims.email, claims=repr(claims))

    assert "voorbeeld.nl" not in stream.getvalue()


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
    configure_logging("INFO", stream=out, envelope=Envelope("1.4.0", "acme-test"))
    set_service("web")
    try:
        with trace_scope() as trace_id:
            bind_level("acme", "balie")
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
        assert (record["organisation"], record["group"]) == ("acme", "balie")
        assert record["service.name"] == "web"
        assert (record["service.version"], record["installation"]) == ("1.4.0", "acme-test")
    assert own["group"] == "other"
    assert "trace.id" not in outside
    assert "organisation" not in outside


def test_a_loose_secret_file_is_reported_by_variable_name_only(stream: io.StringIO) -> None:
    warn_loose_secret_files(["SESSION_SECRET_FILE"])

    (record,) = _records(stream)
    assert record["event.action"] == "settings.secret_file_readable"
    assert record["message"] == "The secret file of SESSION_SECRET_FILE can be read by others."
    assert record["labels.variable"] == "SESSION_SECRET_FILE"


# --- ADR 0103: the line explains itself ----------------------------------------------------


def test_a_line_reads_as_a_sentence_with_its_action_category_and_labels(
    stream: io.StringIO,
) -> None:
    structlog.get_logger().warning(
        "storage.degraded", grade="low", free_percent=6, database_mb=1, reason="free_space_low"
    )

    (record,) = _records(stream)
    assert list(record)[:5] == [
        "@timestamp",
        "log.level",
        "event.category",
        "event.action",
        "message",
    ]
    assert record["message"] == "Disk space is low: 6% free, database 1 MB (free_space_low)."
    assert (record["event.action"], record["event.category"]) == ("storage.degraded", "operations")
    assert (record["labels.free_percent"], record["labels.database_mb"]) == (6, 1)
    assert record["reason"] == "free_space_low"  # an envelope field stays where it is
    assert record["schema"] == SCHEMA
    assert "free_percent" not in record


def test_an_authorisation_decision_is_its_own_category_with_the_actors_kind_and_role(
    stream: io.StringIO,
) -> None:
    with trace_scope():
        bind_actor_role("org_admin")
        structlog.get_logger().info(
            "authz.decision",
            actor="8c1f",
            permission="group.view",
            target="acme/balie",
            target_group="balie",
            target_organisation="acme",
            allowed=True,
            reason="group",
        )

    (record,) = _records(stream)
    assert record["event.category"] == "authz"
    assert record["message"] == ("Permission group.view on acme/balie: allowed yes (group).")
    assert (record["actor.type"], record["actor.role"]) == ("user", "org_admin")
    assert record["labels.allowed"] == "true"


def test_a_library_line_names_its_logger_and_keeps_its_own_text(stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("Waiting for application shutdown.")

    (record,) = _records(stream)
    assert record["event.action"] == "library.uvicorn.error"
    assert record["event.category"] == "operations"
    assert record["message"] == "Waiting for application shutdown."
    assert record["log.logger"] == "uvicorn.error"


def test_the_sentence_carries_a_masked_value_never_the_value(stream: io.StringIO) -> None:
    structlog.get_logger().info("i18n.missing_key", key="booking.title", locale="nl", email="a@b.c")

    (record,) = _records(stream)
    assert "a@b.c" not in stream.getvalue()
    assert record["labels.email"] == MASK
    assert record["message"] == "Text booking.title is missing in nl."


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
        assert record["event.action"] == "http.request"
        assert record["message"] == "GET /g/{slug} answered 200 in 3 ms."
        assert record["log.level"] == "trace"
        assert record["labels.route"] == "/g/{slug}"
        assert record["labels.status"] == 200
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


def _marker(field: str) -> str:
    return f"marker-{field}-x@example.test"


def _foreign_noise() -> None:
    """Lines as libraries write them at DEBUG, TRACE and INFO, holding a marker per PII field."""
    for field in sorted(PII_FIELDS):
        value = _marker(field)
        logging.getLogger("aiosqlite").debug("operation %s parameters %s", "INSERT", (value,))
        logging.getLogger("httpcore.http11").debug(
            "receive_response_headers.complete return_value=%r", (200, [(b"x-" + b"a", value)])
        )
        logging.getLogger("httpcore.connection").log(TRACE, "connect %s", value)
        logging.getLogger("some.future.library").info("payload %s", value)


def _assert_no_marker(text: str) -> None:
    assert "marker-" not in text


@pytest.mark.parametrize("level", ["INFO", "DEBUG", "TRACE"])
def test_a_foreign_logger_stays_at_warning_at_every_level(level: str) -> None:
    """ASVS V16.2.5: only our own loggers follow ``LOG_LEVEL``; any other holds at WARNING."""
    out = io.StringIO()
    taps: list[str] = []
    configure_logging(level, out)
    set_line_tap(taps.append)
    try:
        _foreign_noise()
        set_log_level(level)  # a live level change must not widen it
        _foreign_noise()
        # a library that sets its own level, or one that sets the root, does not widen it either
        logging.getLogger("httpcore").setLevel(TRACE)
        logging.getLogger("some.future.library").setLevel(logging.DEBUG)
        _foreign_noise()
        logging.getLogger("some.future.library").warning("kept %s", "visible")
    finally:
        set_line_tap(None)
        logging.getLogger("httpcore").setLevel(logging.NOTSET)
        logging.getLogger("some.future.library").setLevel(logging.NOTSET)
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    _assert_no_marker(out.getvalue())
    _assert_no_marker("\n".join(taps))
    assert "kept visible" in out.getvalue()  # a warning from a library is still shown
    assert "kept visible" in "\n".join(taps)


@pytest.mark.parametrize("level", ["INFO", "DEBUG", "TRACE"])
async def test_the_real_engine_logs_no_bound_value_at_any_level(level: str) -> None:
    """A real async engine, with the driver loggers asked for everything, shows no marker."""
    out = io.StringIO()
    taps: list[str] = []
    configure_logging(level, out)
    set_line_tap(taps.append)
    for name in ("aiosqlite", "sqlalchemy.engine", "sqlalchemy.pool"):
        logging.getLogger(name).setLevel(TRACE)
    engine = create_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            columns = ", ".join(f"{field} TEXT" for field in sorted(PII_COLUMNS))
            await conn.execute(text(f"CREATE TABLE booking ({columns})"))
            marks = {field: _marker(field) for field in sorted(PII_COLUMNS)}
            names = ", ".join(marks)
            binds = ", ".join(f":{field}" for field in marks)
            insert = f"INSERT INTO booking ({names}) VALUES ({binds})"  # noqa: S608
            await conn.execute(text(insert), marks)
            await conn.execute(text("SELECT * FROM booking"))
    finally:
        await engine.dispose()
        set_line_tap(None)
        for name in ("aiosqlite", "sqlalchemy.engine", "sqlalchemy.pool"):
            logging.getLogger(name).setLevel(logging.NOTSET)
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    _assert_no_marker(out.getvalue())
    _assert_no_marker("\n".join(taps))


def test_our_own_loggers_follow_the_level_and_the_trace_points_work() -> None:
    out = io.StringIO()
    taps: list[str] = []
    configure_logging("TRACE", out)
    set_line_tap(taps.append)
    try:
        logging.getLogger("openplanner.adapters.something").debug("own debug")
        logging.getLogger("uvicorn.error").info("own uvicorn")
        structlog.get_logger().debug("own structlog")
        tracer.trace("outbound.request", method="GET", host="h.example", status=200, ms=2)
        logging.getLogger("httpcore").debug("foreign debug")
    finally:
        set_line_tap(None)
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    messages = [record["message"] for record in _records(out)]
    assert messages == [
        "own debug",
        "own uvicorn",
        "own structlog",
        "GET to h.example answered 200 in 2 ms.",
    ]
    assert [json.loads(line)["message"] for line in taps] == messages


def test_our_own_loggers_stay_quiet_below_the_level() -> None:
    out = io.StringIO()
    configure_logging("INFO", out)
    try:
        logging.getLogger("openplanner.x").debug("hidden")
        logging.getLogger("openplanner.x").info("shown")
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    assert [record["message"] for record in _records(out)] == ["shown"]


def test_a_rendered_parameter_list_is_cut_from_an_exception_text(stream: io.StringIO) -> None:
    """The net under ``hide_parameters``: another engine's exception text loses its values."""
    try:
        raise ValueError(
            "(sqlite3.IntegrityError) NOT NULL\n[SQL: INSERT INTO t (e) VALUES (?)]\n"
            "[parameters: ('ada@example.org',)]\n(Background on this error at: https://x)"
        )
    except ValueError:
        structlog.get_logger().exception("insert.failed")

    output = stream.getvalue()
    assert "ada@example.org" not in output
    assert "[SQL: INSERT INTO t" in output


# --- Library lines that carry the request (found in the security review of PR #532) --------

ADDRESSES = ["203.0.113.7", "2001:db8::7"]
"""A visitor over IPv4 and over IPv6 (documentation addresses, RFC 5737 and RFC 3849)."""
TOKEN = "secret-token-123"
COOKIE = "cookie-secret-456"


class _Seen(logging.Handler):
    """Keeps what a library logger wrote, before the handler of ``configure_logging`` decides."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@dataclass
class _Watch:
    """The fake stream, the tap, and what the watched library logger wrote."""

    out: io.StringIO
    taps: list[str]
    seen: _Seen

    def leaks(self, *secrets: str) -> list[str]:
        """Every line on the stream or the tap that holds one of ``secrets``."""
        lines = [*self.out.getvalue().splitlines(), *self.taps]
        return [line for line in lines if any(secret in line for secret in secrets)]


@contextmanager
def _watching(level: str, library: str) -> Iterator[_Watch]:
    """Configure logging at ``level`` on a fake stream with a tap, and watch ``library``."""
    watch = _Watch(io.StringIO(), [], _Seen())
    configure_logging(level, watch.out)
    set_line_tap(watch.taps.append)
    logging.getLogger(library).addHandler(watch.seen)
    try:
        yield watch
    finally:
        logging.getLogger(library).removeHandler(watch.seen)
        set_line_tap(None)
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


async def _limited_page(request: Request, token: str) -> dict[str, str]:
    return {"page": "ok"}


@pytest.mark.parametrize("address", ADDRESSES)
async def test_a_rate_limit_hit_leaves_no_address_and_no_path_in_any_line(address: str) -> None:
    """slowapi writes ``ratelimit 2 per 1 minute (<address>) exceeded at endpoint: <path>``."""
    limiter = Limiter(key_func=get_remote_address)  # as app.web.protection builds it
    app = FastAPI()
    app.add_api_route("/b/{token}", limiter.limit("2/minute")(_limited_page))
    transport = httpx.ASGITransport(app=app, client=(address, 50000))
    with _watching("INFO", "slowapi") as watch:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            statuses = [(await client.get(f"/b/{TOKEN}")).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    assert any(address in message and TOKEN in message for message in watch.seen.messages)
    assert watch.leaks(address, TOKEN, "/b/") == []


def test_a_slowapi_warning_without_request_data_still_reaches_the_log(
    stream: io.StringIO,
) -> None:
    logging.getLogger("slowapi").warning(
        "Rate limit storage unreachable - falling back to in-memory storage"
    )

    (record,) = _records(stream)
    assert record["event.action"] == "library.slowapi"
    assert str(record["message"]).startswith("Rate limit storage unreachable")


HANDSHAKE = (
    "GET /b/{token}?lang=nl HTTP/1.1\r\n"
    "Host: testserver\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    "Sec-WebSocket-Key: {nonce}\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    "Cookie: op_link={cookie}\r\n"
    "X-Forwarded-For: {address}\r\n"
    "\r\n"
)


async def _websocket_request(address: str) -> tuple[bytes, str]:
    """Send one WebSocket handshake to a real uvicorn that trusts the proxy it comes from.

    Returns the status line and the address the connection came from, as uvicorn saw it.
    """
    config = uvicorn.Config(
        FastAPI(),  # an HTTP application without WebSocket routes, like ours
        host="127.0.0.1",
        port=0,
        log_config=None,
        lifespan="off",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        host, own_port = writer.get_extra_info("sockname")[:2]
        nonce = base64.b64encode(os.urandom(16)).decode()  # 16 random bytes (RFC 6455 §4.1)
        request = HANDSHAKE.format(token=TOKEN, cookie=COOKIE, address=address, nonce=nonce)
        writer.write(request.encode())
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), 5)
        writer.close()
        await writer.wait_closed()
    finally:
        server.should_exit = True
        await serving
    return status, f"{host}:{own_port}"


@pytest.mark.parametrize("level", ["INFO", "DEBUG", "TRACE"])
@pytest.mark.parametrize("address", ADDRESSES)
async def test_a_websocket_request_leaves_no_address_path_or_header_in_any_line(
    address: str, level: str
) -> None:
    """uvicorn writes ``<address> - "WebSocket <path>" 403`` at INFO and the peer at TRACE.

    It also hands ``uvicorn.error`` to the websockets library, which writes the request line
    and every header (cookies, ``X-Forwarded-For``) at DEBUG.
    """
    with _watching(level, "uvicorn.error") as watch:
        status, peer = await _websocket_request(address)

    assert status.startswith(b"HTTP/1.1 403")  # the WebSocket side of uvicorn answered
    assert any(address in message and TOKEN in message for message in watch.seen.messages)
    assert watch.leaks(address, TOKEN, "/b/", COOKIE, peer) == []
    assert "Uvicorn running on" in watch.out.getvalue()  # its start-up lines still come through


def test_uvicorn_error_stays_at_info_when_its_level_is_lowered() -> None:
    """The floor holds in the handler too, when the logger itself is set lower afterwards."""
    uvicorn_error = logging.getLogger("uvicorn.error")
    try:
        with _watching("TRACE", "uvicorn.error") as watch:
            uvicorn_error.setLevel(TRACE)  # as uvicorn's own --log-level trace would
            uvicorn_error.log(TRACE, "%s:%d - HTTP connection made", ADDRESSES[1], 4711)
            uvicorn_error.debug("< cookie: op_link=%s", COOKIE)
            uvicorn_error.info("Application startup complete.")
    finally:
        uvicorn_error.setLevel(logging.INFO)

    assert [json.loads(line)["message"] for line in watch.taps] == ["Application startup complete."]
    assert watch.leaks(ADDRESSES[1], COOKIE) == []
