"""Webex Connect SMS adapter: the request is the SMS API v1 contract (ADR 0022, 0028)."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.adapters.sms import SmsGatewayConfig
from app.adapters.webex_connect_sms import (
    WebexConnectSmsSender,
    content_type,
    sms_body,
)
from app.domain.channels import ChannelKind
from app.domain.ports import IntegrationError, OutboundMessage

CONFIG = SmsGatewayConfig("https://api.us.webexconnect.io/", "service-key", "GemDemo", "31")
MESSAGE = OutboundMessage(ChannelKind.SMS, "0612345678", "", "Herinnering: maandag om 09:00")


def _sender(handler: Callable[[httpx.Request], httpx.Response]) -> WebexConnectSmsSender:
    return WebexConnectSmsSender(CONFIG, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_body_matches_the_sms_api_v1_contract() -> None:
    assert sms_body(MESSAGE, CONFIG, "op-1") == {
        "from": "GemDemo",
        "to": "+31612345678",
        "content": "Herinnering: maandag om 09:00",
        "contentType": "text",
        "correlationId": "op-1",
    }


@pytest.mark.parametrize(
    ("text", "kind"),
    [("Wijzig: https://x.nl/b/abc", "text"), ("café", "text"), ("Groeten 😀", "unicode")],
)
def test_content_type_follows_the_gsm_alphabet(text: str, kind: str) -> None:
    assert content_type(text) == kind


async def test_sends_with_the_service_key_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    await _sender(handler).send(MESSAGE)

    request = seen[0]
    assert str(request.url) == "https://api.us.webexconnect.io/v1/sms/messages"
    assert request.headers["key"] == "service-key"
    body = json.loads(request.content)
    assert body["to"] == "+31612345678"
    assert body["correlationId"].startswith("op-")
    assert "service-key" not in repr(CONFIG)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "unauthorized", False),
        (400, "rejected_400", False),
        (429, "rate_limited", True),
        (503, "server_error", True),
    ],
)
async def test_refusals_map_to_integration_errors(status: int, code: str, retryable: bool) -> None:
    sender = _sender(lambda request: httpx.Response(status, json={"to": "+31612345678"}))

    with pytest.raises(IntegrationError) as error:
        await sender.send(MESSAGE)

    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert "3161234" not in str(error.value)  # the response body is never carried along


async def test_network_failure_is_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(IntegrationError) as error:
        await _sender(handler).send(MESSAGE)
    assert (error.value.code, error.value.retryable) == ("unreachable", True)


async def test_only_sms_is_accepted() -> None:
    email = OutboundMessage(ChannelKind.EMAIL, "a@b.nl", "s", "t")
    with pytest.raises(ValueError, match="SMS"):
        await _sender(lambda request: httpx.Response(201)).send(email)
