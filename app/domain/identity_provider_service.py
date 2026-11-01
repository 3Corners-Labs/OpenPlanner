"""Use cases of the organisation identity providers (ADR 0083 step 2).

The superuser registers a provider for an organisation, changes it, switches it on or off,
rotates its signing key and deletes it (``identity_providers.manage``, locked, fresh login).
An organisation admin and an auditor see the status of their own organisation's providers
(``identity_providers.view``). Every change is audited with the provider key, never a secret
(point 4); a refused discovery check is audited too, with its reason, so the log shows why a
registration did not go through.

    overview / provider      identity_providers.view     status, never a secret
    create                   identity_providers.manage   discovery check, key pair or secret
    update                   identity_providers.manage   re-check when the address changes
    set_enabled / delete     identity_providers.manage
    rotate_key               identity_providers.manage   old key published 24 h longer
    published_keys           (public)                    the JWKS of every provider
    record_test_login        identity_providers.manage   the verdict of a guided test login

The provider itself is never called from here: the adapter fetches the discovery document
through the outbound guard (``DiscoveryFetcher``) and the domain checks it (point 7).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import (
    DiscoveryRefusedError,
    OrganisationProvider,
    ProviderDraft,
    SigningKey,
    check_discovery,
    issuer_of,
    jwks,
    validate_secret,
)
from app.domain.organisations import organisation_by_slug
from app.domain.ports import (
    Clock,
    DiscoveryFetcher,
    IdentityError,
    SigningKeys,
    Transaction,
    UnitOfWork,
)
from app.domain.scheduling import Organisation

CREATED_EVENT = "idp.created"
UPDATED_EVENT = "idp.updated"
ENABLED_EVENT = "idp.enabled"
DISABLED_EVENT = "idp.disabled"
DELETED_EVENT = "idp.deleted"
KEY_ROTATED_EVENT = "idp.key_rotated"
TEST_LOGIN_EVENT = "idp.test_login"
"""The audit actions of ADR 0083 point 4; the detail names the provider key."""

_VIEW = Permission.IDENTITY_PROVIDERS_VIEW
_MANAGE = Permission.IDENTITY_PROVIDERS_MANAGE

_CHANGEABLE = (
    "name",
    "discovery_url",
    "client_id",
    "client_auth",
    "scopes",
    "entitlement_claim",
    "entitlement_prefix",
    "mfa_policy",
    "mfa_amr_values",
    "mfa_acr_values",
)
"""The fields of a draft an update may change; the key is fixed after creation."""


@dataclass(frozen=True)
class ProviderOverview:
    """An organisation with its providers, for the *Login* sub-tab."""

    organisation: Organisation
    providers: list[OrganisationProvider]
    limit: int
    """How many providers the organisation may have (``idp_max_per_organisation``)."""


@dataclass(frozen=True)
class ProviderDetail:
    """One provider with the public side of its keys."""

    organisation: Organisation
    provider: OrganisationProvider
    keys: list[SigningKey]

    @property
    def current_key(self) -> SigningKey | None:
        """The key the provider signs with now, if it has one."""
        return next((k for k in self.keys if k.retired_at is None), None)


class IdentityProviderService:
    """Application service of the organisation providers.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits refusals.
        discovery: Fetches discovery documents through the outbound guard; ``None`` when the
            web process cannot reach out, which refuses every registration.
        keys: Generates signing key pairs.
        limit: Returns ``idp_max_per_organisation`` (class C, read at every call).
        platform_discovery_url: Returns the platform provider's discovery URL, whose issuer
            no organisation provider may share (point 7).
        on_change: Called with the provider key after every change, so a cached adapter is
            rebuilt.
    """

    def __init__(  # noqa: PLR0913
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        discovery: DiscoveryFetcher | None,
        keys: SigningKeys,
        *,
        limit: Callable[[], int],
        platform_discovery_url: Callable[[], str | None],
        on_change: Callable[[str], None] = lambda _: None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._discovery = discovery
        self._keys = keys
        self._limit = limit
        self._platform_discovery_url = platform_discovery_url
        self._on_change = on_change

    # --- reading -------------------------------------------------------------------------

    async def overview(self, principal: Principal, slug: str) -> ProviderOverview:
        """Return the organisation's providers.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        require(await self._admin.authorize(principal, _VIEW, organisation_slug=slug))
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            providers = [
                p for p in await tx.list_identity_providers(_everything()) if p.organisation == slug
            ]
        return ProviderOverview(organisation, providers, self._limit())

    async def provider(self, principal: Principal, slug: str, key: str) -> ProviderDetail:
        """Return one provider of the organisation with its keys.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``identity_provider_not_found``.
        """
        require(await self._admin.authorize(principal, _VIEW, organisation_slug=slug))
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            provider = await _provider_of(tx, slug, key)
            keys = await tx.get_signing_keys(key)
        return ProviderDetail(organisation, provider, keys)

    async def published_keys(self) -> dict[str, list[dict[str, str]]]:
        """Return the JWKS of every provider: current keys and those retired within a day."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            keys = await tx.published_signing_keys(now)
        return jwks([k for k in keys if k.published(now)])

    # --- changing ------------------------------------------------------------------------

    async def create(
        self, principal: Principal, slug: str, draft: ProviderDraft, secret: str | None
    ) -> OrganisationProvider:
        """Register a provider for the organisation, switched off.

        ``private_key_jwt`` gets a generated key pair; ``client_secret_basic`` needs
        ``secret``. The discovery document is fetched and checked first.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``identity_provider_limit_reached``, ``idp_key_taken``,
                ``invalid_configuration`` (``client_secret``), ``discovery_refused``,
                ``issuer_taken`` or ``integration_not_ready`` (no outbound fetcher).
        """
        require(await self._admin.authorize(principal, _MANAGE))
        if draft.client_auth.needs_secret:
            validate_secret(secret or "")
        issuer = await self._checked_issuer(principal, slug, draft, CREATED_EVENT)
        now = self._clock.now()
        generated = self._keys.generate() if draft.client_auth.needs_key else None
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            others = [
                p for p in await tx.list_identity_providers(_everything()) if p.key != draft.key
            ]
            if await tx.get_identity_provider(draft.key) is not None:
                raise DomainError(ErrorCode.IDP_KEY_TAKEN, "key")
            if sum(p.organisation == slug for p in others) >= self._limit():
                raise DomainError(ErrorCode.IDENTITY_PROVIDER_LIMIT_REACHED)
            self._check_issuer_unique(issuer, others)
            provider = await tx.add_identity_provider(
                OrganisationProvider(
                    id=None,
                    organisation=organisation.slug,
                    key=draft.key,
                    name=draft.name,
                    discovery_url=draft.discovery_url,
                    issuer=issuer,
                    client_id=draft.client_id,
                    client_auth=draft.client_auth,
                    scopes=draft.scopes,
                    entitlement_claim=draft.entitlement_claim,
                    entitlement_prefix=draft.entitlement_prefix,
                    mfa_policy=draft.mfa_policy,
                    mfa_amr_values=draft.mfa_amr_values,
                    mfa_acr_values=draft.mfa_acr_values,
                    enabled=False,
                    secret_set=secret is not None,
                    created_at=now,
                    updated_at=now,
                    checked_at=now,
                ),
                secret if draft.client_auth.needs_secret else None,
            )
            if generated is not None:
                await tx.add_signing_key(provider.key, generated, now)
            await self._audit(tx, principal, CREATED_EVENT, provider, f"key:{provider.key}")
        self._on_change(provider.key)
        return provider

    async def update(
        self,
        principal: Principal,
        slug: str,
        key: str,
        draft: ProviderDraft,
        secret: str | None,
    ) -> list[str]:
        """Change a provider; return the names of the fields that changed.

        A changed discovery address is checked again. Switching to ``private_key_jwt``
        generates a key pair when the provider has none; switching to
        ``client_secret_basic`` needs a secret unless one is stored. A blank secret keeps the
        stored one.

        Raises:
            DomainError: As ``create``, and ``identity_provider_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        if draft.key != key:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
        if secret is not None:
            validate_secret(secret)
        async with self._uow.read() as tx:
            current = await _provider_of(tx, slug, key)
        changed = [f for f in _CHANGEABLE if getattr(current, f) != getattr(draft, f)]
        if secret is not None:
            changed.append("client_secret")
        if draft.client_auth.needs_secret and secret is None and not current.secret_set:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "client_secret")
        if not changed:
            return []
        rechecked = "discovery_url" in changed or "client_auth" in changed
        issuer = (
            await self._checked_issuer(principal, slug, draft, UPDATED_EVENT)
            if rechecked
            else current.issuer
        )
        now = self._clock.now()
        async with self._uow.locked() as tx:
            stored = await _provider_of(tx, slug, key)
            others = [p for p in await tx.list_identity_providers(_everything()) if p.key != key]
            self._check_issuer_unique(issuer, others)
            updated = replace(
                stored,
                name=draft.name,
                discovery_url=draft.discovery_url,
                issuer=issuer,
                client_id=draft.client_id,
                client_auth=draft.client_auth,
                scopes=draft.scopes,
                entitlement_claim=draft.entitlement_claim,
                entitlement_prefix=draft.entitlement_prefix,
                mfa_policy=draft.mfa_policy,
                mfa_amr_values=draft.mfa_amr_values,
                mfa_acr_values=draft.mfa_acr_values,
                secret_set=stored.secret_set or secret is not None,
                updated_at=now,
                checked_at=now if rechecked else stored.checked_at,
            )
            await tx.save_identity_provider(updated, secret)
            if updated.needs_key and not any(
                k.retired_at is None for k in await tx.get_signing_keys(key)
            ):
                await tx.add_signing_key(key, self._keys.generate(), now)
                changed.append("signing_key")
            await self._audit(tx, principal, UPDATED_EVENT, updated, ",".join(changed))
        self._on_change(key)
        return changed

    async def set_enabled(self, principal: Principal, slug: str, key: str, enabled: bool) -> bool:
        """Switch the provider on or off; return whether that changed anything.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``identity_provider_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        async with self._uow.locked() as tx:
            provider = await _provider_of(tx, slug, key)
            if provider.enabled == enabled:
                return False
            updated = replace(provider, enabled=enabled, updated_at=self._clock.now())
            await tx.save_identity_provider(updated, None)
            event = ENABLED_EVENT if enabled else DISABLED_EVENT
            await self._audit(tx, principal, event, updated, f"key:{key}")
        self._on_change(key)
        return True

    async def delete(self, principal: Principal, slug: str, key: str) -> None:
        """Remove the provider with its secret and keys; its sessions end (step 3).

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``identity_provider_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        async with self._uow.locked() as tx:
            provider = await _provider_of(tx, slug, key)
            await tx.delete_identity_provider(key)
            await self._audit(tx, principal, DELETED_EVENT, provider, f"key:{key}")
        self._on_change(key)

    async def rotate_key(self, principal: Principal, slug: str, key: str) -> SigningKey:
        """Generate a new signing key; the old one stays published for ``KEY_OVERLAP``.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``identity_provider_not_found`` or ``invalid_configuration`` (the provider
                authenticates with a secret, so it has no key to rotate).
        """
        require(await self._admin.authorize(principal, _MANAGE))
        generated = self._keys.generate()
        now = self._clock.now()
        async with self._uow.locked() as tx:
            provider = await _provider_of(tx, slug, key)
            if not provider.needs_key:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "client_auth")
            await tx.add_signing_key(key, generated, now)
            await tx.retire_signing_keys(key, keep=generated.kid, at=now)
            await tx.save_identity_provider(replace(provider, updated_at=now), None)
            await self._audit(tx, principal, KEY_ROTATED_EVENT, provider, f"key:{key}")
        self._on_change(key)
        return SigningKey(key, generated.kid, generated.public_jwk, now)

    async def record_test_login(
        self, principal: Principal, slug: str, key: str, *, reason: str = ""
    ) -> None:
        """Audit the verdict of a guided test login (point 9); ``reason`` says what failed."""
        require(await self._admin.authorize(principal, _MANAGE))
        outcome = Outcome.SUCCESS if not reason else Outcome.FAILURE
        await self._admin.record(
            principal.subject, TEST_LOGIN_EVENT, outcome, reason, slug, None, f"key:{key}"
        )

    # --- helpers -------------------------------------------------------------------------

    async def _checked_issuer(
        self, principal: Principal, slug: str, draft: ProviderDraft, event: str
    ) -> str:
        """Fetch and check the discovery document; audit a refusal with its reason.

        Raises:
            DomainError: ``integration_not_ready`` without a fetcher, ``discovery_refused``
                otherwise (field ``discovery_url``).
        """
        if self._discovery is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "discovery_url")
        try:
            document = await self._discovery.fetch(draft.discovery_url)
            return check_discovery(document, draft.discovery_url, draft.client_auth)
        except (IdentityError, DiscoveryRefusedError) as refused:
            reason = refused.code if isinstance(refused, IdentityError) else refused.reason
            await self._admin.record(
                principal.subject, event, Outcome.FAILURE, reason, slug, None, f"key:{draft.key}"
            )
            raise DomainError(ErrorCode.DISCOVERY_REFUSED, "discovery_url") from refused

    def _check_issuer_unique(self, issuer: str, others: list[OrganisationProvider]) -> None:
        """Refuse an issuer the platform provider or another provider already has.

        Raises:
            DomainError: ``issuer_taken`` (field ``discovery_url``).
        """
        taken = {p.issuer.rstrip("/") for p in others}
        platform = self._platform_discovery_url()
        if platform:
            taken.add(issuer_of(platform).rstrip("/"))
        if issuer.rstrip("/") in taken:
            raise DomainError(ErrorCode.ISSUER_TAKEN, "discovery_url")

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        action: str,
        provider: OrganisationProvider,
        detail: str,
    ) -> None:
        """Record a change in the same transaction, at the organisation's level."""
        await tx.add_audit_event(
            AuditEvent(
                self._clock.now(),
                principal.subject,
                action,
                Outcome.SUCCESS,
                "",
                provider.organisation,
                None,
                detail,
            )
        )


def _everything() -> Scope:
    """The scope of every organisation; the service narrows to one organisation by slug."""
    return Scope(everything=True)


async def _provider_of(tx: Transaction, slug: str, key: str) -> OrganisationProvider:
    """Return the provider ``key`` of organisation ``slug``.

    Raises:
        DomainError: ``organisation_not_found`` or ``identity_provider_not_found`` (also
            when the provider belongs to another organisation: it does not exist there).
    """
    await organisation_by_slug(tx, slug)
    provider = await tx.get_identity_provider(key)
    if provider is None or provider.organisation != slug:
        raise DomainError(ErrorCode.IDENTITY_PROVIDER_NOT_FOUND)
    return provider
