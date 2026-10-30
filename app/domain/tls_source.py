"""The certificate source of the bundled proxy, managed in the portal (ADR 0066).

::

    superuser ─► upload(chain, key) ─► PemReader.read ─► validate ─► stored, key sealed ─► audit
              ─► choose("own")      ─► a certificate is stored? ─► SettingsService.change
              ─► sync()             ─► source own and stored? ─► CertificateDelivery.publish
                                                         else ─► CertificateDelivery.withdraw

The private key passes through ``upload`` once, as text the reader normalised, and is never
read back by the domain: the delivery adapter opens it where the proxy needs it. Every refusal
carries a code, never a byte of the upload. The worker's check (``app.domain.tls_check``) stays
the only report of what citizens see; this module only says what was uploaded.
"""

from collections.abc import Callable
from datetime import datetime

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import (
    MAX_PEM_BYTES,
    OWN,
    REMOVE_EVENT,
    SOURCE_KEY,
    UPLOAD_EVENT,
    OwnCertificate,
    validate,
)
from app.domain.ports import CertificateDelivery, Clock, PemReader, Transaction, UnitOfWork
from app.domain.settings_service import SettingsService
from app.domain.tls import CertificateCheck
from app.domain.tls_check import TlsWatch

_PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE


class CertificateSourceService:
    """Use cases of the certificate source and the uploaded pair (superuser).

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        settings: Changes the source setting (lock, bounds and audit are its job).
        reader: Parses an upload.
        delivery: Hands the pair to the proxy.
        watch: Probes the public address for *Check now*.
        host: Returns the host of the public address the certificate must cover.
        bundled: Returns whether the bundled proxy runs; otherwise nothing here applies.
        source: Returns the effective source (``acme`` or ``own``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        settings: SettingsService,
        reader: PemReader,
        delivery: CertificateDelivery,
        watch: TlsWatch,
        *,
        host: Callable[[], str | None],
        bundled: Callable[[], bool],
        source: Callable[[], str],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._settings = settings
        self._reader = reader
        self._delivery = delivery
        self._watch = watch
        self._host = host
        self._bundled = bundled
        self._source = source

    def applicable(self) -> bool:
        """Whether the portal manages the source: only with the bundled proxy."""
        return self._bundled()

    async def uploaded(self) -> OwnCertificate | None:
        """Return the stored own certificate for the page, if there is one."""
        async with self._uow.read() as tx:
            return await tx.get_own_certificate()

    async def upload(self, principal: Principal, chain: bytes, key: bytes) -> OwnCertificate:
        """Validate and store a pair, replacing any earlier one; deliver it if in use.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_certificate_unreadable``, ``tls_key_mismatch``, ``tls_name_mismatch``,
                ``tls_certificate_expired``, ``tls_certificate_not_yet_valid``,
                ``setting_no_key``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        try:
            self._require_applicable()
            if len(chain) > MAX_PEM_BYTES or len(key) > MAX_PEM_BYTES:
                raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
            pair = self._reader.read(chain, key)
            validate(pair, self._host(), now)
            stored = OwnCertificate(pair.certificate, pair.fingerprint, pair.chain_pem, now)
            async with self._uow.locked() as tx:
                await tx.save_own_certificate(stored, pair.key_pem)
                await _audit(tx, now, principal.subject, UPLOAD_EVENT, stored)
        except DomainError as error:
            await self._refused(principal, error, UPLOAD_EVENT)
            raise
        if self._source() == OWN:
            await self._delivery.publish(stored.fingerprint)
        return stored

    async def remove(self, principal: Principal) -> bool:
        """Remove the stored pair; refused while the source is ``own``.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_certificate_in_use``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        try:
            self._require_applicable()
            if self._source() == OWN:
                raise DomainError(ErrorCode.TLS_CERTIFICATE_IN_USE)
            async with self._uow.locked() as tx:
                stored = await tx.get_own_certificate()
                if stored is None:
                    return False
                await tx.delete_own_certificate()
                await _audit(tx, now, principal.subject, REMOVE_EVENT, stored)
        except DomainError as error:
            await self._refused(principal, error, REMOVE_EVENT)
            raise
        await self._delivery.withdraw()
        return True

    async def choose(self, principal: Principal, raw: str) -> None:
        """Set the source; ``own`` needs a stored pair. Delivers the result at once.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_certificate_missing``, and what ``SettingsService.change`` raises.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        chosen = raw.strip().lower()
        try:
            self._require_applicable()
            if chosen == OWN and await self.uploaded() is None:
                raise DomainError(ErrorCode.TLS_CERTIFICATE_MISSING, SOURCE_KEY)
        except DomainError as error:
            await self._refused(principal, error, f"set:{SOURCE_KEY}")
            raise
        await self._settings.change(principal, SOURCE_KEY, chosen)
        await self.sync(chosen)

    async def sync(self, source: str | None = None) -> str:
        """Make the delivery match the effective source; return ``published`` or ``withdrawn``.

        Called at start-up and whenever the portal values change, so a reset, a CLI change or
        an environment lock reach the proxy the same way as a change in the portal.
        """
        if (source or self._source()) == OWN:
            stored = await self.uploaded()
            if stored is not None:
                await self._delivery.publish(stored.fingerprint)
                return "published"
        await self._delivery.withdraw()
        return "withdrawn"

    async def check_now(self, principal: Principal) -> CertificateCheck:
        """Probe the public address at once and store the outcome (*Check now*).

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        return await self._watch.check()

    def _require_applicable(self) -> None:
        """Raise unless the bundled proxy runs."""
        if not self._bundled():
            raise DomainError(ErrorCode.TLS_SOURCE_NOT_APPLICABLE, SOURCE_KEY)

    async def _refused(self, principal: Principal, error: DomainError, what: str) -> None:
        """Audit a refusal with its code; never with any content."""
        await self._admin.record(
            principal.subject,
            _PERMISSION.value,
            Outcome.FAILURE,
            reason=error.code.value,
            detail=what,
        )


async def _audit(
    tx: Transaction, now: datetime, actor: str, event: str, stored: OwnCertificate
) -> None:
    """Record an upload or removal: the fingerprint and the expiry date, nothing else."""
    expiry = stored.certificate.not_after.date().isoformat()
    await tx.add_audit_event(
        AuditEvent(
            now,
            actor,
            _PERMISSION.value,
            Outcome.SUCCESS,
            detail=f"{event}:{stored.fingerprint}:{expiry}",
        )
    )
