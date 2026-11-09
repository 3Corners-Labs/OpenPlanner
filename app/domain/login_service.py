"""Turning a login's claims into a principal, at login and on every request (ADR 0049).

The OIDC adapter extracts ``LoginClaims``; this service gives them meaning with the directory
(role keys, organisations and groups with their IdP values) under the claim mode of the
installation, through ``app.domain.entitlements.resolve``:

    callback ──complete_login(claims)──► refuse (ambiguous_claims, too_many_entitlements)
                                       └─ record the login, the refused values, a shadow
                                          mismatch ──► LoginOutcome (principal, session claims)
    every request ──principal_for(claims)──► Principal (grants resolved afresh, so a renamed
                                              or deleted organisation or group takes effect),
                                              or None when the session's organisation
                                              provider is off, deleted or switched off on
                                              the host (ADR 0083 point 17)
    dry run ──preview(claims, prefix)──► Resolution under a proposed prefix (ADR 0042 §8)

The mode (class B) and the prefix (class C) are read through ``settings`` on every call, so a
prefix changed in the portal applies at once. A login through an organisation provider (ADR
0083) resolves in ``entitlements`` mode with that provider's prefix, confined to its
organisation (point 2), at login and on every request.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.admin import AdminService
from app.domain.authorization import Principal, RoleCatalogue
from app.domain.entitlements import (
    ClaimMode,
    Directory,
    LoginClaims,
    Resolution,
    recognised_values,
    resolve,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity import PLATFORM, provider_of
from app.domain.identity_providers import OrganisationProvider
from app.domain.ports import UnitOfWork
from app.domain.staff import StaffStatus

AMBIGUOUS_CLAIMS = "ambiguous_claims"
"""Refusal reason of ADR 0049 point 14: the legacy claims hand out a right nobody gave."""

TOO_MANY_ENTITLEMENTS = "too_many_entitlements"
"""Refusal reason of ADR 0049 point 6: more recognised values than a session can hold."""

SUSPENDED = "suspended"
"""Refusal reason of ADR 0046 point 7: an administrator suspended the person (claims are moot)."""


class LoginRefusedError(DomainError):
    """The claims were extracted fine, but the resolution refuses to make an admin of them.

    Args:
        reason: ``ambiguous_claims`` or ``too_many_entitlements``.
    """

    def __init__(self, reason: str) -> None:
        """Store the reason; the field names it for the error page."""
        super().__init__(ErrorCode.LOGIN_REFUSED, reason)
        self.reason = reason


@dataclass(frozen=True)
class ClaimSettings:
    """What the resolution needs from the settings: the mode and the prefix."""

    mode: ClaimMode
    prefix: str


@dataclass(frozen=True)
class LoginOutcome:
    """A completed login: the admin, and the claims the session should keep.

    Attributes:
        resolution: What the claims resolved to.
        session_claims: ``claims`` with the entitlement values narrowed to the recognised
            ones, so the session cookie stays small (ADR 0049 point 9).
    """

    resolution: Resolution
    session_claims: LoginClaims

    @property
    def principal(self) -> Principal:
        """The admin as resolved."""
        return self.resolution.principal


class LoginService:
    """Application service of the login resolution.

    Args:
        uow: Opens database transactions (the directory is read once per call).
        admin: Records logins and refusals.
        settings: Returns the current claim mode and prefix.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        admin: AdminService,
        settings: Callable[[], ClaimSettings],
        organisation_idps: Callable[[], bool] = lambda: False,
    ) -> None:
        """Store the collaborators; ``organisation_idps`` is the host's switch (ADR 0083)."""
        self._uow = uow
        self._admin = admin
        self._settings = settings
        self._organisation_idps = organisation_idps

    async def directory(self) -> tuple[Directory, RoleCatalogue]:
        """Return the directory and the role catalogue, read in one transaction."""
        async with self._uow.read() as tx:
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            directory = await tx.get_directory()
        return directory, catalogue

    async def principal_for(self, claims: LoginClaims) -> Principal | None:
        """Return the admin the session's claims amount to now (one call per request).

        ``None`` ends the session: its organisation provider is switched off, deleted, or
        the host switched organisation providers off (ADR 0083 point 17, B14).
        """
        key = provider_of(claims.subject)
        if key == PLATFORM:
            return (await self._resolve(claims, self._settings().prefix)).principal
        provider = await self._live_provider(key)
        if provider is None:
            return None
        directory, catalogue = await self.directory()
        return resolve(
            claims,
            ClaimMode.ENTITLEMENTS,
            provider.entitlement_prefix,
            directory,
            catalogue,
            provider.organisation,
        ).principal

    async def preview(self, claims: LoginClaims, prefix: str) -> Resolution:
        """Return what ``claims`` resolve to under a proposed ``prefix`` (the dry run)."""
        return await self._resolve(claims, prefix)

    async def complete_login(
        self, claims: LoginClaims, provider: OrganisationProvider | None = None
    ) -> LoginOutcome:
        """Resolve a fresh login, record it and return what the session should keep.

        ``provider`` is the organisation provider the login came through, if any: the claims
        then resolve in ``entitlements`` mode with its prefix, confined to its organisation
        (ADR 0083 point 2); values outside it are refused and recorded like any other.

        Raises:
            LoginRefusedError: ``ambiguous_claims`` (ADR 0049 point 14) or
                ``too_many_entitlements`` (point 6), both audited as a denied login; or
                ``suspended`` (ADR 0046 point 7), which the login route audits.
        """
        settings = self._settings()
        mode, prefix = settings.mode, settings.prefix
        confined_to = None
        if provider is not None:
            mode, prefix = ClaimMode.ENTITLEMENTS, provider.entitlement_prefix
            confined_to = provider.organisation
        async with self._uow.read() as tx:
            known = await tx.get_staff(claims.subject)
        if known is not None and known.status is StaffStatus.SUSPENDED:
            raise LoginRefusedError(SUSPENDED)
        directory, catalogue = await self.directory()
        resolution = resolve(claims, mode, prefix, directory, catalogue, confined_to)
        if resolution.ambiguous:
            await self._admin.record_refused_login(claims.subject, AMBIGUOUS_CLAIMS)
            raise LoginRefusedError(AMBIGUOUS_CLAIMS)
        if resolution.too_many:
            await self._admin.record_refused_login(claims.subject, TOO_MANY_ENTITLEMENTS)
            raise LoginRefusedError(TOO_MANY_ENTITLEMENTS)
        await self._admin.record_login(
            resolution.principal, resolution.refused, resolution.mismatch, claims.email
        )
        kept = LoginClaims(
            subject=claims.subject,
            display_name=claims.display_name,
            roles=claims.roles,
            organisations=claims.organisations,
            groups=claims.groups,
            role_claims=claims.role_claims,
            entitlements=recognised_values(claims, prefix, directory, confined_to),
        )
        return LoginOutcome(resolution, kept)

    async def _live_provider(self, key: str) -> OrganisationProvider | None:
        """Return the organisation provider ``key`` if it may carry sessions now."""
        if not self._organisation_idps():
            return None
        async with self._uow.read() as tx:
            provider = await tx.get_identity_provider(key)
        return provider if provider is not None and provider.enabled else None

    async def _resolve(self, claims: LoginClaims, prefix: str) -> Resolution:
        """Resolve ``claims`` under the current mode and ``prefix``."""
        directory, catalogue = await self.directory()
        return resolve(claims, self._settings().mode, prefix, directory, catalogue)
