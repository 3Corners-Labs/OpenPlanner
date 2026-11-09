"""Reading a SQLite configuration package under the rules of ADR 0099 point 14.

The SQLite package of ADR 0089 is no longer exported. It is still read for one release: the
``ConfigurationTransfer`` adapter converts it to a ``replace`` document of the platform, and
that document is planned and applied like any other (rights per change, the in-use rules, one
write transaction, an audit event per change). The service checks the passphrase and the
size, audits the import with counts, and never logs a passphrase or a value. An import has no
review step, as the package import never had: the caller confirms by calling it, and the
document service is told so (``confirmed``).
"""

from collections.abc import Callable

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.configuration_package import IMPORT_DETAIL, PERMISSION, PackageSummary
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, ConfigurationTransfer


class ConfigurationPackageService:
    """Inspects and imports configuration packages.

    Args:
        transfer: The adapter that reads the SQLite files and converts them to a document.
        documents: Plans and applies the converted document.
        clock: Current time.
        admin: Writes the audit events.
        max_bytes: The largest package accepted (``CONFIGURATION_PACKAGE_MAX_MB``).
    """

    def __init__(
        self,
        transfer: ConfigurationTransfer,
        documents: ConfigurationDocumentService,
        clock: Clock,
        admin: AdminService,
        *,
        max_bytes: Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._transfer = transfer
        self._documents = documents
        self._clock = clock
        self._admin = admin
        self._max_bytes = max_bytes

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        """Return what ``package`` holds, after checking it opens with ``passphrase``.

        Raises:
            DomainError: ``configuration_package_too_large``, ``configuration_package_invalid``
                or ``configuration_passphrase_wrong``.
        """
        self._check_size(package)
        return await self._transfer.inspect(package, passphrase)

    async def import_(self, actor: Actor, package: bytes, passphrase: str) -> PackageSummary:
        """Replace this installation's configuration with ``package``, as a document; audited.

        Raises:
            DomainError: The codes of ``inspect``; ``configuration_package_version`` when the
                schema revisions differ; ``integration_not_ready`` without a settings key;
                the refusal that stopped the plan (a right the actor lacks, an object in use,
                a value the portal would refuse); ``configuration_too_many_changes``.
        """
        try:
            self._check_size(package)
            document, summary = await self._transfer.to_document(package, passphrase)
            result = await self._documents.apply_document(actor, document, None, confirmed=True)
        except DomainError as error:
            await self._refused(actor.subject, error)
            raise
        if not result.applied:
            refusal = result.plan.refusals[0]
            refused = DomainError(refusal.code, refusal.field or refusal.path)
            await self._refused(actor.subject, refused)
            raise refused
        await self._admin.record(
            actor.subject, PERMISSION.value, Outcome.SUCCESS, detail=summary.detail
        )
        return summary

    def _check_size(self, package: bytes) -> None:
        """Refuse an empty package or one above the limit."""
        if not package:
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
        if len(package) > self._max_bytes():
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "package")

    async def _refused(self, actor: str, error: DomainError) -> None:
        """Audit a refusal with its code; never the passphrase or the file."""
        await self._admin.record(
            actor, PERMISSION.value, Outcome.DENIED, error.code.value, detail=IMPORT_DETAIL
        )
