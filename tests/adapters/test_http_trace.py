"""The ``outbound.request`` trace point on every outgoing HTTP call (ADR 0085 point 9)."""

import httpx
import pytest

from app.adapters.http_trace import traced_transport
from tests.support.tracing import captured_trace


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=traced_transport(httpx.MockTransport(handler)))  # type: ignore[arg-type]


async def test_a_response_leaves_method_host_status_and_duration_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"email": "x@example.org"})

    with captured_trace() as lines:
        async with _client(handler) as client:
            await client.post(
                "https://api.example.net/v1/meetings?token=secret-token",
                headers={"Authorization": "Bearer secret-bearer"},
                json={"title": "trial"},
            )

    assert len(lines) == 1
    assert lines[0].point == "outbound.request"
    assert lines[0].fields["method"] == "POST"
    assert lines[0].fields["host"] == "api.example.net"
    assert lines[0].fields["status"] == 201
    assert isinstance(lines[0].fields["ms"], int)
    assert "secret" not in repr(lines)
    assert "/v1/meetings" not in repr(lines)


async def test_a_failed_connection_leaves_the_error_type_and_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with captured_trace() as lines, pytest.raises(httpx.ConnectError):
        async with _client(handler) as client:
            await client.get("https://down.example.net/")

    assert lines[0].fields["error"] == "ConnectError"
    assert "status" not in lines[0].fields


def test_the_default_transport_is_wrapped_too() -> None:
    transport = traced_transport(None)
    assert isinstance(transport, httpx.AsyncBaseTransport)
