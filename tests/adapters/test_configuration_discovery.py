"""The issuer of a provider named by a document comes from its discovery document."""

import pytest

from app.adapters.configuration_discovery import issuer_fetcher
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.identity_providers import DISCOVERY_URL, ISSUER, FakeDiscoveryFetcher


async def test_the_issuer_is_the_one_the_checked_document_announces() -> None:
    issuer_of = issuer_fetcher(FakeDiscoveryFetcher(), production=True)

    assert await issuer_of(DISCOVERY_URL, "client_secret_basic") == ISSUER


async def test_a_document_that_cannot_be_fetched_is_refused() -> None:
    fetcher = FakeDiscoveryFetcher()
    fetcher.fail = "not_public"

    with pytest.raises(DomainError) as refused:
        await issuer_fetcher(fetcher, production=True)(DISCOVERY_URL, "client_secret_basic")

    assert refused.value.code is ErrorCode.DISCOVERY_REFUSED
    assert refused.value.field == "discovery_url"


async def test_a_document_whose_issuer_is_not_the_address_base_is_refused_in_production() -> None:
    fetcher = FakeDiscoveryFetcher()
    url = "https://other.voorbeeld.nl/.well-known/openid-configuration"
    fetcher.documents[url] = fetcher.documents[DISCOVERY_URL]

    with pytest.raises(DomainError) as refused:
        await issuer_fetcher(fetcher, production=True)(url, "client_secret_basic")
    outside_production = await issuer_fetcher(fetcher, production=False)(url, "client_secret_basic")

    assert refused.value.code is ErrorCode.DISCOVERY_REFUSED
    assert outside_production == ISSUER  # pinned as announced, as the portal does


async def test_a_client_auth_method_that_is_not_ours_is_refused() -> None:
    issuer_of = issuer_fetcher(FakeDiscoveryFetcher(), production=True)

    with pytest.raises(DomainError) as refused:
        await issuer_of(DISCOVERY_URL, "magic")

    assert refused.value.code is ErrorCode.DISCOVERY_REFUSED
