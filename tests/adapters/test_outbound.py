"""The outbound guard for addresses typed in the portal (ADR 0083 point 8, abuse case B8)."""

from collections.abc import Callable

import httpx
import pytest

from app.adapters.outbound import (
    MAX_RESPONSE_BYTES,
    OutboundRefusedError,
    guarded_request,
    is_public,
)


def resolver(*addresses: str) -> Callable[[str, int], list[str]]:
    def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - an address to refuse, not one to bind
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_b8_internal_addresses_are_not_public(address: str) -> None:
    assert not is_public(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "2001:4860:4860::8888"])
def test_internet_addresses_are_public(address: str) -> None:
    assert is_public(address)


async def test_the_request_goes_to_the_checked_address_with_the_name_for_tls() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with mock(handler) as http:
        response = await guarded_request(
            http,
            "GET",
            "https://idp.example.nl/.well-known/openid-configuration",
            resolve=resolver("8.8.8.8"),
        )

    assert response.json() == {"ok": True}
    sent = seen[0]
    assert sent.url.host == "8.8.8.8"
    assert sent.url.path == "/.well-known/openid-configuration"
    assert sent.headers["host"] == "idp.example.nl"
    assert sent.extensions["sni_hostname"] == "idp.example.nl"


async def test_a_port_and_an_ipv6_address_are_kept() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    async with mock(handler) as http:
        await guarded_request(
            http, "GET", "https://idp.example.nl:8443/x?a=1", resolve=resolver("2001:4860::1")
        )

    assert seen[0].url.host == "2001:4860::1"
    assert seen[0].url.port == 8443
    assert seen[0].url.query == b"a=1"
    assert seen[0].headers["host"] == "idp.example.nl:8443"


@pytest.mark.parametrize(
    ("url", "addresses", "reason"),
    [
        ("http://idp.example.nl/x", ("8.8.8.8",), "not_https"),
        ("https:///x", ("8.8.8.8",), "no_host"),
        ("https://idp.example.nl/x", ("169.254.169.254",), "not_public"),
        ("https://idp.example.nl/x", ("8.8.8.8", "10.0.0.1"), "not_public"),
        ("https://idp.example.nl/x", (), "unresolvable"),
        ("https://user:pw@idp.example.nl/x", ("8.8.8.8",), "credentials_in_url"),
    ],
)
async def test_b8_refused_before_anything_is_sent(
    url: str, addresses: tuple[str, ...], reason: str
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    async with mock(handler) as http:
        with pytest.raises(OutboundRefusedError) as refused:
            await guarded_request(http, "GET", url, resolve=resolver(*addresses))

    assert refused.value.reason == reason
    assert sent == []


async def test_b8_a_redirect_is_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    async with mock(handler) as http:
        with pytest.raises(OutboundRefusedError) as refused:
            await guarded_request(
                http, "GET", "https://idp.example.nl/x", resolve=resolver("8.8.8.8")
            )

    assert refused.value.reason == "redirect"


async def test_a_response_larger_than_the_cap_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    async with mock(handler) as http:
        with pytest.raises(OutboundRefusedError) as refused:
            await guarded_request(
                http, "GET", "https://idp.example.nl/x", resolve=resolver("8.8.8.8")
            )

    assert refused.value.reason == "too_large"


async def test_development_may_reach_a_lab_host_over_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    async with mock(handler) as http:
        await guarded_request(
            http,
            "GET",
            "http://keycloak:8080/realms/andere",
            resolve=resolver("172.18.0.5"),
            allow_private=True,
        )

    assert seen[0].url.host == "172.18.0.5"
    assert "sni_hostname" not in seen[0].extensions


async def test_a_post_carries_its_form() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with mock(handler) as http:
        await guarded_request(
            http,
            "POST",
            "https://idp.example.nl/token",
            resolve=resolver("8.8.8.8"),
            data={"grant_type": "authorization_code"},
        )

    assert seen[0].content == b"grant_type=authorization_code"
