"""The OIDC adapters of the registered organisation providers (ADR 0083 step 2).

One ``OidcIdentityProvider`` per provider key, built from the stored registration and its
opened credentials (``ProviderCredentialStore``) and kept while the registration does not
change (``updated_at``), so the provider metadata cache of the adapter survives between
logins. The portal's registration service calls ``invalidate`` after every change.

An organisation provider resolves entitlements only (ADR 0083 point 2): its claim mapping
names no legacy role, organisation or group claim.
"""

from collections.abc import Callable
from datetime import datetime

import httpx

from app.adapters.db.identity_providers import ProviderCredentialStore
from app.adapters.oidc import ClaimMapping, Guard, OidcConfig, OidcIdentityProvider
from app.domain.mfa import MFA_AMR_VALUES


class OrganisationProviders:
    """Builds and caches the adapter of each registered provider.

    Args:
        store: Opens the registrations with their credentials.
        http: Shared HTTP client.
        guard: How the providers' addresses are fetched (relaxed in development and test).
        max_entitlements: Returns ``oidc_entitlements_max_values``, read at every build.
    """

    def __init__(
        self,
        store: ProviderCredentialStore,
        http: httpx.AsyncClient,
        *,
        guard: Guard,
        max_entitlements: Callable[[], int],
    ) -> None:
        """Store the collaborators; nothing is built until a provider is asked for."""
        self._store = store
        self._http = http
        self._guard = guard
        self._max_entitlements = max_entitlements
        self._built: dict[str, tuple[datetime, OidcIdentityProvider]] = {}

    async def provider(self, key: str) -> OidcIdentityProvider | None:
        """Return the adapter of provider ``key``, or ``None`` when it is not registered."""
        found = await self._store.credentials(key)
        if found is None:
            self._built.pop(key, None)
            return None
        cached = self._built.get(key)
        if cached is not None and cached[0] == found.provider.updated_at:
            return cached[1]
        registration = found.provider
        config = OidcConfig(
            discovery_url=registration.discovery_url,
            client_id=registration.client_id,
            client_secret=found.client_secret or "",
            scopes=registration.scopes,
            mapping=ClaimMapping(
                role_claim="",
                organisation_claim="",
                group_claim="",
                role_values={},
                entitlement_claim=registration.entitlement_claim,
                entitlement_prefix=registration.entitlement_prefix,
                max_entitlements=self._max_entitlements(),
            ),
            provider_key=key,
            client_auth=registration.client_auth,
            signing_key=(found.kid, found.private_pem) if found.kid and found.private_pem else None,
            guard=self._guard,
            expected_issuer=registration.issuer,
            mfa_policy=registration.mfa_policy,
            mfa_amr_values=registration.mfa_amr_values or " ".join(sorted(MFA_AMR_VALUES)),
            mfa_acr_values=registration.mfa_acr_values,
        )
        adapter = OidcIdentityProvider(config, self._http)
        self._built[key] = (registration.updated_at, adapter)
        return adapter

    def invalidate(self, key: str) -> None:
        """Forget the adapter of ``key``; the next request rebuilds it."""
        self._built.pop(key, None)
