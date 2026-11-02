"""The httpx instance probe: what each kind of answer or failure becomes (ADR 0086)."""

import ssl

import httpx
import pytest

from app.adapters.instance_probe import HttpxInstanceProbe
from app.domain.public_address import Forwarded, InstanceAnswer, InstanceReply

URL = "https://afspraak.example.nl"


def probing(handler: httpx.MockTransport) -> HttpxInstanceProbe:
    return HttpxInstanceProbe(transport=handler)


async def test_the_probe_asks_the_endpoint_with_the_nonce_and_follows_no_redirect() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"instance_id": "abc", "scheme": "https", "forwarded": "honoured"}
        )

    reply = await probing(httpx.MockTransport(answer)).probe(URL, "n0nce")

    assert reply == InstanceReply(InstanceAnswer("abc", "https", Forwarded.HONOURED))
    assert str(seen[0].url) == f"{URL}/api/v1/instance?probe=n0nce"
    assert "cookie" not in seen[0].headers
    assert "authorization" not in seen[0].headers


async def test_an_answer_without_the_detail_is_still_an_answer() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"instance_id": "abc"}))

    reply = await probing(transport).probe(URL, "n")

    assert reply.answer == InstanceAnswer("abc")


async def test_a_redirect_is_a_finding_and_is_not_followed() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(301, headers={"location": "https://www.example.nl/"})

    reply = await probing(httpx.MockTransport(handler)).probe(URL, "n")

    assert reply == InstanceReply(redirected=True)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(502), "http_502"),
        (httpx.Response(404, text="nope"), "http_404"),
        (httpx.Response(200, text="<html>"), "unreadable"),
        (httpx.Response(200, json=["x"]), "unreadable"),
        (httpx.Response(200, json={"instance_id": 7}), "unreadable"),
        (httpx.Response(200, json={"instance_id": "x" * 65}), "unreadable"),
    ],
)
async def test_an_answer_that_is_not_ours_is_named_by_the_providers_status(
    response: httpx.Response, error: str
) -> None:
    transport = httpx.MockTransport(lambda _: response)

    reply = await probing(transport).probe(URL, "n")

    assert reply == InstanceReply(error=error)
    assert "nope" not in repr(reply)


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "unreachable"),
        (httpx.ReadError("reset"), "unreachable"),
    ],
)
async def test_a_network_failure_becomes_a_word(failure: httpx.HTTPError, error: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise failure

    reply = await probing(httpx.MockTransport(handler)).probe(URL, "n")

    assert reply == InstanceReply(error=error)


async def test_a_refused_certificate_is_told_apart_from_a_refused_connection() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("bad certificate")
        except ssl.SSLError as cause:
            raise httpx.ConnectError("handshake") from cause

    reply = await probing(httpx.MockTransport(handler)).probe(URL, "n")

    assert reply == InstanceReply(error="tls")


async def test_an_unknown_forwarded_word_and_scheme_are_ignored() -> None:
    body = {"instance_id": "abc", "scheme": "gopher", "forwarded": "maybe"}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))

    reply = await probing(transport).probe(URL, "n")

    assert reply.answer == InstanceAnswer("abc")
