"""Turning a login's claims into a principal, at login and on every request (ADR 0049).

The OIDC adapter extracts ``LoginClaims``; this service gives them meaning with the directory
(role keys, organisations and groups with their IdP values) under the claim mode of the
installation, through ``app.domain.entitlements.resolve``:

    callback ──complete_login(claims)──► refuse (ambiguous_claims, too_many_entitlements)
                                       └─ record the login, the refused values, a shadow
                                          mismatch ──► LoginOutcome (principal, session claims)
    every request ──principal_for(claims)──► Principal (grants resolved afresh, so a renamed
                                              or deleted organisation or group takes effect)
    dry run ──preview(claims, prefix)──► Resolution under a proposed prefix (ADR 0042 §8)

The mode (class B) and the prefix (class C) are read through ``settings`` on every call, so a
prefix changed in the portal applies at once.
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
from app.domain.ports import UnitOfWork

AMBIGUOUS_CLAIMS = "ambiguous_claims"
"""Refusal reason of ADR 0049 point 14: the legacy claims hand out a right nobody gave."""

TOO_MANY_ENTITLEMENTS = "too_many_entitlements"
"""Refusal reason of ADR 0049 point 6: more recognised values than a session can hold."""


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
        self, uow: UnitOfWork, admin: AdminService, settings: Callable[[], ClaimSettings]
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._admin = admin
        self._settings = settings

    async def directory(self) -> tuple[Directory, RoleCatalogue]:
        """Return the directory and the role catalogue, read in one transaction."""
        async with self._uow.read() as tx:
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            directory = await tx.get_directory()
        return directory, catalogue

    async def principal_for(self, claims: LoginClaims) -> Principal:
        """Return the admin the session's claims amount to now (one call per request)."""
        return (await self._resolve(claims, self._settings().prefix)).principal

    async def preview(self, claims: LoginClaims, prefix: str) -> Resolution:
        """Return what ``claims`` resolve to under a proposed ``prefix`` (the dry run)."""
        return await self._resolve(claims, prefix)

    async def complete_login(self, claims: LoginClaims) -> LoginOutcome:
        """Resolve a fresh login, record it and return what the session should keep.

        Raises:
            LoginRefusedError: ``ambiguous_claims`` (ADR 0049 point 14) or
                ``too_many_entitlements`` (point 6); both are audited as a denied login.
        """
        settings = self._settings()
        directory, catalogue = await self.directory()
        resolution = resolve(claims, settings.mode, settings.prefix, directory, catalogue)
        if resolution.ambiguous:
            await self._admin.record_refused_login(claims.subject, AMBIGUOUS_CLAIMS)
            raise LoginRefusedError(AMBIGUOUS_CLAIMS)
        if resolution.too_many:
            await self._admin.record_refused_login(claims.subject, TOO_MANY_ENTITLEMENTS)
            raise LoginRefusedError(TOO_MANY_ENTITLEMENTS)
        await self._admin.record_login(
            resolution.principal, resolution.refused, resolution.mismatch
        )
        kept = LoginClaims(
            subject=claims.subject,
            display_name=claims.display_name,
            roles=claims.roles,
            organisations=claims.organisations,
            groups=claims.groups,
            role_claims=claims.role_claims,
            entitlements=recognised_values(claims, settings.prefix, directory),
        )
        return LoginOutcome(resolution, kept)

    async def _resolve(self, claims: LoginClaims, prefix: str) -> Resolution:
        """Resolve ``claims`` under the current mode and ``prefix``."""
        directory, catalogue = await self.directory()
        return resolve(claims, self._settings().mode, prefix, directory, catalogue)
