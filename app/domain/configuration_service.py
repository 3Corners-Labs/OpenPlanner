"""Export and import of configuration packages under the rules of ADR 0089.

The service checks the passphrase and the size, refuses an import into an installation that
is in use, hands the SQLite work to the ``ConfigurationTransfer`` adapter and audits what
happened with codes and counts, never a passphrase or a value.
"""

from collections.abc import Callable

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.configuration_package import (
    EXPORT_DETAIL,
    IMPORT_DETAIL,
    PERMISSION,
    PackageSummary,
    check_passphrase,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, ConfigurationTransfer


class ConfigurationPackageService:
    """Exports and imports configuration packages under the rules of ADR 0089.

    Args:
        transfer: The adapter that reads and writes the SQLite files.
        clock: Current time, for audit events.
        admin: Writes the audit events.
        max_bytes: The largest package accepted for import (``CONFIGURATION_PACKAGE_MAX_MB``).
    """

    def __init__(
        self,
        transfer: ConfigurationTransfer,
        clock: Clock,
        admin: AdminService,
        *,
        max_bytes: Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._transfer = transfer
        self._clock = clock
        self._admin = admin
        self._max_bytes = max_bytes

    async def export(self, actor: str, passphrase: str) -> bytes:
        """Return a package of this installation sealed under ``passphrase``; audited.

        Raises:
            DomainError: ``configuration_passphrase_short``; ``integration_not_ready`` when
                the installation has no settings key to open its secrets with.
        """
        try:
            check_passphrase(passphrase)
            package = await self._transfer.export(passphrase)
        except DomainError as error:
            await self._refused(actor, error, EXPORT_DETAIL)
            raise
        await self._admin.record(actor, PERMISSION.value, Outcome.SUCCESS, detail=EXPORT_DETAIL)
        return package

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        """Return what ``package`` holds, after checking it opens with ``passphrase``.

        Raises:
            DomainError: ``configuration_package_too_large``, ``configuration_package_invalid``
                or ``configuration_passphrase_wrong``.
        """
        self._check_size(package)
        return await self._transfer.inspect(package, passphrase)

    async def import_(self, actor: str, package: bytes, passphrase: str) -> PackageSummary:
        """Replace this installation's configuration with ``package``; audited.

        Raises:
            DomainError: The codes of ``inspect``; ``configuration_installation_in_use`` when
                the installation has bookings, slots or an active closure;
                ``configuration_package_version`` when the schema revisions differ;
                ``integration_not_ready`` without a settings key.
        """
        try:
            self._check_size(package)
            if await self._transfer.in_use():
                raise DomainError(ErrorCode.CONFIGURATION_INSTALLATION_IN_USE, "package")
            summary = await self._transfer.import_(package, passphrase)
        except DomainError as error:
            await self._refused(actor, error, IMPORT_DETAIL)
            raise
        await self._admin.record(actor, PERMISSION.value, Outcome.SUCCESS, detail=summary.detail)
        return summary

    def _check_size(self, package: bytes) -> None:
        """Refuse an empty package or one above the limit."""
        if not package:
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
        if len(package) > self._max_bytes():
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "package")

    async def _refused(self, actor: str, error: DomainError, detail: str) -> None:
        """Audit a refusal with its code; never the passphrase or the file."""
        await self._admin.record(
            actor, PERMISSION.value, Outcome.DENIED, error.code.value, detail=detail
        )
