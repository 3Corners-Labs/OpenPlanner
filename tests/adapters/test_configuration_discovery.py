"""The issuer of a provider named by a document comes from its discovery document."""

from collections.abc import Mapping

import pytest

from app.adapters.configuration_discovery import issuer_fetcher
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import IdentityError

URL = "https://idp.voorbeeld.nl/realms/x/.well-known/openid-configuration"
DOCUMENT = {
    "issuer": "https://idp.voorbeeld.nl/realms/x",
    "authorization_endpoint": "https://idp.voorbeeld.nl/a",
    "token_endpoint": "https://idp.voorbeeld.nl/t",
    "jwks_uri": "https://idp.voorbeeld.nl/k",
    "response_types_supported": ["code"],
    "code_challenge_methods_supported": ["S256"],
    "id_token_signing_alg_values_supported": ["ES256"],
    "token_endpoint_auth_methods_supported": ["client_secret_basic", "private_key_jwt"],
    "token_endpoint_auth_signing_alg_values_supported": ["ES256"],
}


class Fetcher:
    def __init__(self, document: Mapping[str, object] | None = None, error: str | None = None):
        self.document = document
        self.error = error

    async def fetch(self, url: str) -> Mapping[str, object]:
        if self.error:
            raise IdentityError(self.error)
        assert self.document is not None
        return self.document


async def test_the_issuer_is_the_one_the_checked_document_announces() -> None:
    issuer_of = issuer_fetcher(Fetcher(DOCUMENT), production=True)

    assert await issuer_of(URL, "client_secret_basic") == "https://idp.voorbeeld.nl/realms/x"


@pytest.mark.parametrize(
    "fetcher",
    [Fetcher(error="not_public"), Fetcher({"issuer": "https://elsewhere/"}), Fetcher(DOCUMENT)],
)
async def test_a_document_that_cannot_be_fetched_or_checked_is_refused(fetcher: Fetcher) -> None:
    issuer_of = issuer_fetcher(fetcher, production=True)

    with pytest.raises(DomainError) as refused:
        await issuer_of(
            URL if fetcher.document is not DOCUMENT else URL + "x", "client_secret_basic"
        )

    assert refused.value.code is ErrorCode.DISCOVERY_REFUSED
    assert refused.value.field == "discovery_url"


async def test_a_client_auth_method_that_is_not_ours_is_refused() -> None:
    issuer_of = issuer_fetcher(Fetcher(DOCUMENT), production=True)

    with pytest.raises(DomainError) as refused:
        await issuer_of(URL, "magic")

    assert refused.value.code is ErrorCode.DISCOVERY_REFUSED
