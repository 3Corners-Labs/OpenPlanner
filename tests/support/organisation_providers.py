"""Identity provider ``org-a`` of organisation demo in tests (ADR 0083): its values and a login.

``NEW_PROVIDER`` holds the values an administrator registers, as the portal's form sends them;
``draft`` is the same provider for the domain service. ``tests.support.organisation_login``
registers it through the portal.
"""

from app.domain.entitlements import LoginClaims
from app.domain.identity_providers import ProviderDraft
from app.domain.scheduling import Organisation
from tests.fakes.identity_providers import DISCOVERY_URL

NEW_PROVIDER = {
    "key": "org-a",
    "name": "Acme A",
    "discovery_url": DISCOVERY_URL,
    "client_id": "openplanner",
    "client_auth": "private_key_jwt",
    "scopes": "openid profile email",
    "entitlement_claim": "openplanner_entitlements",
    "entitlement_prefix": "openplanner",
}
"""Provider ``org-a`` as the registration form sends it."""
PLATFORM_DISCOVERY = "https://login.platform.example/.well-known/openid-configuration"
"""The discovery address of the platform's own identity provider."""
OTHER_ISSUER = "https://login.org-b.example"
"""The issuer of a second provider, ``org-b``."""
OTHER_DISCOVERY = f"{OTHER_ISSUER}/.well-known/openid-configuration"
"""The discovery address of ``OTHER_ISSUER``."""
ORGANISATIONS = (Organisation(1, "Demo-acme", "demo"), Organisation(2, "Andere", "andere"))
"""The organisations of the identity provider domain tests: ``demo`` and ``andere``."""


def draft(**overrides: str) -> ProviderDraft:
    """Return provider ``org-a`` as a draft for the domain service, with any value overridden."""
    return ProviderDraft.create(**{**NEW_PROVIDER, **overrides})


def organisation_claims(entitlements: frozenset[str] = frozenset()) -> LoginClaims:
    """Return the login of Ada at provider ``org-a``, holding ``entitlements`` only."""
    return LoginClaims(
        subject="idp:org-a:ada",
        display_name="Ada",
        roles=frozenset(),
        organisations=frozenset(),
        groups=frozenset(),
        role_claims=frozenset(),
        entitlements=entitlements,
    )
