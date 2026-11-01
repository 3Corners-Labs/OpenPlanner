"""The wire adapters of the log destinations, against recorded shapes and local receivers."""

import asyncio
import json
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.adapters.db.log_destinations import LogTarget
from app.adapters.log_senders import build_sender, syslog_message
from app.adapters.log_shipping import LogTester
from app.domain.log_destinations import DeliveryError, DestinationKind, Reason
from tests.domain.test_log_destinations import destination
from tests.fakes.clock import FixedClock

LINE = json.dumps(
    {"@timestamp": "2026-11-01T09:00:00.123Z", "log.level": "error", "message": "job.failed"}
)
AUDIT = json.dumps(
    {
        "@timestamp": "2026-11-01T09:00:01.000Z",
        "log.level": "info",
        "log_type": "audit",
        "event.category": "audit",
        "message": "admin.login",
    }
)


def timeout() -> float:
    return 2.0


def recording(
    status: int = 200, body: object = None
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return seen, httpx.MockTransport(handler)


def sender_for(
    kind: DestinationKind, transport: httpx.AsyncBaseTransport | None, **changes: object
):
    addresses = {
        DestinationKind.SPLUNK_HEC: "https://splunk.example.org:8088",
        DestinationKind.ELASTIC: "https://es.example.org:9200",
        DestinationKind.HTTP: "https://collector.example.org/logs",
    }
    values: dict[str, object] = {"kind": kind, "address": addresses[kind], "index": ""}
    values.update(changes)
    target = LogTarget(destination(**values), changes.pop("secret_value", "secret-token"))  # type: ignore[arg-type]
    return build_sender(target, timeout, transport)


# --- Splunk HEC --------------------------------------------------------------------------


async def test_splunk_gets_one_event_per_line_with_time_index_and_token() -> None:
    seen, transport = recording()
    sender = sender_for(DestinationKind.SPLUNK_HEC, transport, index="openplanner", source="web-1")

    await sender.send([LINE, AUDIT])

    (request,) = seen
    assert str(request.url) == "https://splunk.example.org:8088/services/collector/event"
    assert request.headers["authorization"] == "Splunk secret-token"
    events = json.loads("[" + request.content.decode().replace("}{", "},{") + "]")
    assert [e["event"]["message"] for e in events] == ["job.failed", "admin.login"]
    assert events[0]["index"] == "openplanner"
    assert events[0]["source"] == "web-1"
    assert events[0]["sourcetype"] == "_json"
    assert events[0]["time"] == datetime(2026, 11, 1, 9, 0, 0, 123000, tzinfo=UTC).timestamp()


async def test_splunk_keeps_a_path_the_admin_gave() -> None:
    seen, transport = recording()
    sender = sender_for(
        DestinationKind.SPLUNK_HEC,
        transport,
        address="https://splunk.example.org:8088/services/collector/event/1.0",
    )

    await sender.send([LINE])

    assert seen[0].url.path == "/services/collector/event/1.0"


# --- Elastic -----------------------------------------------------------------------------


async def test_elastic_gets_a_bulk_create_into_the_data_stream() -> None:
    seen, transport = recording(body={"errors": False, "items": []})
    sender = sender_for(DestinationKind.ELASTIC, transport, index="logs-openplanner-prod")

    await sender.send([LINE, AUDIT])

    (request,) = seen
    assert str(request.url) == "https://es.example.org:9200/_bulk"
    assert request.headers["authorization"] == "ApiKey secret-token"
    assert request.headers["content-type"] == "application/x-ndjson"
    rows = request.content.decode().splitlines()
    assert json.loads(rows[0]) == {"create": {"_index": "logs-openplanner-prod"}}
    assert rows[1] == LINE
    assert rows[2] == rows[0]
    assert request.content.endswith(b"\n")


async def test_elastic_defaults_the_data_stream_and_drops_a_batch_with_item_errors() -> None:
    seen, transport = recording(body={"errors": True, "items": []})
    sender = sender_for(DestinationKind.ELASTIC, transport)

    with pytest.raises(DeliveryError) as caught:
        await sender.send([LINE])

    assert json.loads(seen[0].content.decode().splitlines()[0])["create"]["_index"] == (
        "logs-openplanner-default"
    )
    assert (caught.value.reason, caught.value.drop) == (Reason.REFUSED, True)


# --- generic HTTP ------------------------------------------------------------------------


async def test_http_posts_ndjson_with_an_optional_bearer_token() -> None:
    seen, transport = recording(204)
    with_token = sender_for(DestinationKind.HTTP, transport)
    plain = build_sender(
        LogTarget(
            destination(kind=DestinationKind.HTTP, address="https://collector.example.org/logs"),
            None,
        ),
        timeout,
        transport,
    )

    await with_token.send([LINE, AUDIT])
    await plain.send([LINE])

    assert seen[0].content == f"{LINE}\n{AUDIT}\n".encode()
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "authorization" not in seen[1].headers


@pytest.mark.parametrize(
    ("status", "reason", "drop"),
    [
        (401, Reason.REFUSED, False),  # fix the credential and the same batch goes through
        (403, Reason.REFUSED, False),
        (404, Reason.REFUSED, False),
        (400, Reason.REFUSED, True),  # malformed for the platform: never retry
        (413, Reason.REFUSED, True),
        (422, Reason.REFUSED, True),
        (429, Reason.SERVER, False),
        (503, Reason.SERVER, False),
        (500, Reason.SERVER, False),
    ],
)
async def test_statuses_map_to_a_reason_and_whether_the_batch_is_hopeless(
    status: int, reason: Reason, drop: bool
) -> None:
    _, transport = recording(status, body={"detail": "secret-looking text from the platform"})
    sender = sender_for(DestinationKind.HTTP, transport)

    with pytest.raises(DeliveryError) as caught:
        await sender.send([LINE])

    error = caught.value
    assert (error.reason, error.code, error.drop) == (reason, status, drop)
    assert "secret" not in repr(error.args)  # never the platform's own text


async def test_redirects_are_not_followed() -> None:
    seen, transport = recording(302)
    sender = sender_for(DestinationKind.HTTP, transport)

    with pytest.raises(DeliveryError) as caught:
        await sender.send([LINE])

    assert len(seen) == 1
    assert caught.value.code == 302


async def test_a_connection_error_is_unreachable_and_a_certificate_error_is_tls() -> None:
    def failing(exc: Exception) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            raise exc

        return httpx.MockTransport(handler)

    down = sender_for(DestinationKind.HTTP, failing(httpx.ConnectError("refused")))
    slow = sender_for(DestinationKind.HTTP, failing(httpx.ReadTimeout("slow")))
    untrusted_cause = ssl.SSLCertVerificationError("self signed certificate")
    chained = httpx.ConnectError("tls")
    chained.__cause__ = untrusted_cause
    untrusted = sender_for(DestinationKind.HTTP, failing(chained))

    for sender, expected in (
        (down, Reason.UNREACHABLE),
        (slow, Reason.UNREACHABLE),
        (untrusted, Reason.TLS),
    ):
        with pytest.raises(DeliveryError) as caught:
            await sender.send([LINE])
        assert caught.value.reason is expected
        assert caught.value.code is None


async def test_an_unreadable_extra_authority_is_a_tls_problem_not_a_crash() -> None:
    sender = sender_for(
        DestinationKind.HTTP,
        None,
        ca_pem="-----BEGIN CERTIFICATE-----\nnot\n-----END CERTIFICATE-----",
    )

    with pytest.raises(DeliveryError) as caught:
        await sender.send([LINE])

    assert caught.value.reason is Reason.TLS


# --- syslog messages ---------------------------------------------------------------------


def test_a_syslog_message_is_rfc_5424_with_the_json_line_as_message() -> None:
    message = syslog_message(LINE, "web-1").decode()

    assert message == f"<131>1 2026-11-01T09:00:00.123Z web-1 openplanner - log - {LINE}"


def test_priority_follows_the_level_and_the_msgid_follows_the_line_type() -> None:
    audit = syslog_message(AUDIT, "").decode()
    trace = syslog_message(json.dumps({"log.level": "trace", "@timestamp": "t"}), "h").decode()

    assert audit.startswith("<134>1 2026-11-01T09:00:01.000Z - openplanner - audit - ")
    assert trace.startswith("<135>1 t h openplanner - log - ")  # local0 * 8 + debug


# --- syslog on the wire ------------------------------------------------------------------


def _self_signed(host: str, directory: Path) -> tuple[str, Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_text(pem)
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return pem, cert_file, key_file


class Receiver:
    def __init__(self) -> None:
        self.data = b""
        self.connections = 0


def _handler(receiver: Receiver):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        receiver.connections += 1
        try:
            while chunk := await reader.read(65536):
                receiver.data += chunk
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    return handle


async def _serve(receiver: Receiver, context: ssl.SSLContext | None) -> asyncio.Server:
    return await asyncio.start_server(_handler(receiver), "localhost", 0, ssl=context)


def _port(server: asyncio.Server) -> int:
    return int(server.sockets[0].getsockname()[1])


def _frames(data: bytes) -> list[str]:
    """Split RFC 6587 octet-counted frames."""
    frames = []
    while data:
        length, _, data = data.partition(b" ")
        frames.append(data[: int(length)].decode())
        data = data[int(length) :]
    return frames


def syslog_target(address: str, **changes: object) -> LogTarget:
    return LogTarget(
        destination(kind=DestinationKind.SYSLOG, address=address, secret_set=False, **changes), None
    )


async def test_tcp_syslog_sends_octet_counted_frames_on_one_connection() -> None:
    receiver = Receiver()
    server = await _serve(receiver, None)
    sender = build_sender(
        syslog_target(f"syslog+tcp://localhost:{_port(server)}", source="web-1"), timeout
    )

    await sender.send([LINE])
    await sender.send([AUDIT])
    await sender.aclose()
    await asyncio.sleep(0.05)
    server.close()
    await server.wait_closed()

    assert receiver.connections == 1
    assert _frames(receiver.data) == [
        syslog_message(LINE, "web-1").decode(),
        syslog_message(AUDIT, "web-1").decode(),
    ]


async def test_tls_syslog_trusts_the_destination_authority_and_refuses_an_unknown_one(
    tmp_path: Path,
) -> None:
    pem, cert_file, key_file = _self_signed("localhost", tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_file, key_file)
    receiver = Receiver()
    server = await _serve(receiver, server_context)
    address = f"syslog+tls://localhost:{_port(server)}"

    trusted = build_sender(syslog_target(address, ca_pem=pem), timeout)
    await trusted.send([LINE])
    await trusted.aclose()
    await asyncio.sleep(0.05)
    assert _frames(receiver.data) == [syslog_message(LINE, "").decode()]

    unknown = build_sender(syslog_target(address), timeout)
    with pytest.raises(DeliveryError) as caught:
        await unknown.send([LINE])
    server.close()
    await server.wait_closed()

    assert caught.value.reason is Reason.TLS


async def test_a_closed_port_is_unreachable_and_the_next_send_reconnects() -> None:
    first = Receiver()
    server = await _serve(first, None)
    port = _port(server)
    sender = build_sender(syslog_target(f"syslog+tcp://localhost:{port}"), timeout)
    server.close()
    await server.wait_closed()

    with pytest.raises(DeliveryError) as caught:
        await sender.send([LINE])
    assert caught.value.reason is Reason.UNREACHABLE

    second = Receiver()
    again = await asyncio.start_server(_handler(second), "localhost", port)  # the platform is back
    await sender.send([LINE])
    await sender.aclose()
    await asyncio.sleep(0.05)
    again.close()
    await again.wait_closed()

    assert _frames(second.data) == [syslog_message(LINE, "").decode()]


async def test_udp_syslog_sends_one_datagram_per_line() -> None:
    received: list[bytes] = []

    class Collect(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: object) -> None:
            received.append(data)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(Collect, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    sender = build_sender(syslog_target(f"syslog+udp://127.0.0.1:{port}"), timeout)

    await sender.send([LINE, AUDIT])
    await asyncio.sleep(0.1)
    await sender.aclose()
    transport.close()

    assert received == [syslog_message(LINE, ""), syslog_message(AUDIT, "")]


# --- the test line -----------------------------------------------------------------------


class Source:
    def __init__(self, target: LogTarget | None) -> None:
        self.target = target

    async def one(self, destination_id: int) -> LogTarget | None:
        return self.target


async def test_the_tester_sends_one_test_line_and_closes_the_sender() -> None:
    seen, transport = recording()
    target = LogTarget(
        destination(id=3, kind=DestinationKind.HTTP, address="https://c.example.org/x"), None
    )
    closed: list[bool] = []

    def factory(t: LogTarget, t_out: Callable[[], float]):
        sender = build_sender(t, t_out, transport)
        original = sender.aclose

        async def aclose() -> None:
            closed.append(True)
            await original()

        sender.aclose = aclose  # type: ignore[method-assign]
        return sender

    await LogTester(Source(target), factory, FixedClock(), "web")(3)

    record = json.loads(seen[0].content.decode().strip())
    assert (record["message"], record["destination"]) == ("logging.test", 3)
    assert closed == [True]


async def test_the_tester_says_not_ready_for_an_unreadable_or_a_gone_destination() -> None:
    def factory(t: LogTarget, t_out: Callable[[], float]):  # pragma: no cover - never built
        raise AssertionError("no sender for an unreadable destination")

    unreadable = LogTarget(destination(id=3), None, unreadable=True)
    for source in (Source(unreadable), Source(None)):
        with pytest.raises(DeliveryError) as caught:
            await LogTester(source, factory, FixedClock(), "web")(3)
        assert caught.value.reason is Reason.NOT_READY
