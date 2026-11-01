"""The adapters of registered organisation providers pin the stored issuer (ASVS V10.5.3)."""

from typing import Any

import httpx

from app.adapters.db.identity_providers import ProviderCredentials
from app.adapters.idp_registry import OrganisationProviders
from app.adapters.oidc import Guard
from app.domain.identity_providers import ClientAuth, OrganisationProvider
from tests.fakes.identity_providers import DISCOVERY_URL, ISSUER
from tests.support.domain import NOW


class _Store:
    """Hands out one stored registration."""

    def __init__(self, found: ProviderCredentials) -> None:
        self.found = found

    async def credentials(self, key: str) -> ProviderCredentials | None:
        return self.found


def _registration(**overrides: Any) -> ProviderCredentials:
    values: dict[str, Any] = {
        "id": 1,
        "organisation": "gem-a",
        "key": "gem-a",
        "name": "Gemeente A",
        "discovery_url": DISCOVERY_URL,
        "issuer": ISSUER,
        "client_id": "openplanner",
        "client_auth": ClientAuth.CLIENT_SECRET_BASIC,
        "scopes": "openid",
        "entitlement_claim": "e",
        "entitlement_prefix": "openplanner",
        "enabled": True,
        "secret_set": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ProviderCredentials(OrganisationProvider(**values), "s", None, None)


async def test_the_adapter_pins_the_issuer_stored_at_registration() -> None:
    async with httpx.AsyncClient() as http:
        registry = OrganisationProviders(
            _Store(_registration(issuer="https://stored.example")),  # type: ignore[arg-type]
            http,
            guard=Guard(),
            max_entitlements=lambda: 200,
        )
        adapter = await registry.provider("gem-a")
    assert adapter is not None
    assert adapter._config.issuer == "https://stored.example"  # noqa: SLF001
