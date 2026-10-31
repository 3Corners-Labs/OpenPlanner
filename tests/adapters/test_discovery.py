"""The discovery fetch behind the outbound guard (ADR 0083 points 7 and 8)."""

import httpx
import pytest

from app.adapters.discovery import GuardedDiscoveryFetcher
from app.domain.ports import IdentityError
from tests.adapters.test_outbound import mock, resolver
from tests.fakes.identity_providers import DISCOVERY_URL, discovery_document

PUBLIC = resolver("8.8.8.8")


async def test_a_public_document_is_returned_as_an_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/openid-configuration"
        return httpx.Response(200, json=discovery_document())

    async with mock(handler) as http:
        document = await GuardedDiscoveryFetcher(http, resolve=PUBLIC).fetch(DISCOVERY_URL)

    assert document["issuer"] == "https://login.gem-a.example"


async def test_b8_an_internal_address_is_refused_with_the_guards_reason() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=discovery_document())

    async with mock(handler) as http:
        fetcher = GuardedDiscoveryFetcher(http, resolve=resolver("169.254.169.254"))
        with pytest.raises(IdentityError) as refused:
            await fetcher.fetch(DISCOVERY_URL)

    assert refused.value.code == "not_public"
    assert sent == []


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, content=b"not json"), httpx.Response(200, json=[1])],
)
async def test_anything_but_a_json_object_is_a_failed_discovery(response: httpx.Response) -> None:
    async with mock(lambda _: response) as http:
        with pytest.raises(IdentityError) as failed:
            await GuardedDiscoveryFetcher(http, resolve=PUBLIC).fetch(DISCOVERY_URL)

    assert failed.value.code == "discovery_failed"


async def test_a_lab_provider_on_an_internal_address_needs_the_relaxed_guard() -> None:
    async with mock(lambda _: httpx.Response(200, json=discovery_document())) as http:
        fetcher = GuardedDiscoveryFetcher(http, allow_private=True, resolve=resolver("10.0.0.5"))
        assert (await fetcher.fetch(DISCOVERY_URL))["issuer"]
