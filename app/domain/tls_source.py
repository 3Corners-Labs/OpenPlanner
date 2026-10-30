"""The certificate source of the bundled proxy, managed in the portal (ADR 0070).

::

    superuser ─► generate_csr()        ─► CertificateVolume.generate_pending ─► CSR to download
              ─► upload(chain[, key])  ─► PemReader ─► validate ─► key on this host? ─► stored
              ─► choose("own")         ─► chain stored and its key here? ─► SettingsService
              ─► sync()                ─► own, chain and key? ─► CertificateVolume.publish
                                                           else ─► CertificateVolume.withdraw

The private key lives on the volume the proxy reads and nowhere else: generated there for a
certificate signing request (the default path), or imported straight into it next to its
chain (a migration). The database holds the chain and the leaf's public key, so a restore on
another host shows *key missing* until a new request is made or the key imported again.
Every refusal carries a code, never a byte of an upload. The worker's check
(``app.domain.tls_check``) stays the only report of what participants see; this module only says
what was uploaded and whether this host can serve it.
"""

from collections.abc import Callable
from datetime import datetime

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import (
    GENERATE_EVENT,
    IMPORT_EVENT,
    MAX_PEM_BYTES,
    OWN,
    REMOVE_EVENT,
    SOURCE_KEY,
    UPLOAD_EVENT,
    OwnCertificate,
    SourceStatus,
    validate,
)
from app.domain.ports import CertificateVolume, Clock, PemReader, Transaction, UnitOfWork
from app.domain.settings_service import SettingsService
from app.domain.tls import CertificateCheck
from app.domain.tls_check import TlsWatch

_PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE


class CertificateSourceService:
    """Use cases of the certificate source, the key on this host and the chain (superuser).

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        settings: Changes the source setting (lock, bounds and audit are its job).
        reader: Parses an upload.
        volume: The directory shared with the proxy: keys, request, served pair.
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
        volume: CertificateVolume,
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
        self._volume = volume
        self._watch = watch
        self._host = host
        self._bundled = bundled
        self._source = source

    def applicable(self) -> bool:
        """Whether the portal manages the source: only with the bundled proxy."""
        return self._bundled()

    async def status(self) -> SourceStatus:
        """Return the stored chain, whether its key is on this host, and a pending request."""
        async with self._uow.read() as tx:
            uploaded = await tx.get_own_certificate()
        keys = self._volume.keys()
        on_host = uploaded is not None and keys.current == uploaded.public_key
        return SourceStatus(uploaded, on_host, keys.pending_csr)

    async def generate_csr(self, principal: Principal) -> str:
        """Make a new key on this host and return its certificate signing request (PEM).

        The key waits next to the served one until the chain for it is uploaded, so a
        certificate in use is never disturbed. A request made earlier is replaced.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_volume_unwritable``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        try:
            host = self._require_host()
            csr = self._volume.generate_pending(host)
            async with self._uow.locked() as tx:
                await _audit(tx, now, principal.subject, GENERATE_EVENT)
        except DomainError as error:
            await self._refused(principal, error, GENERATE_EVENT)
            raise
        return csr

    async def upload(
        self, principal: Principal, chain: bytes, key: bytes | None = None
    ) -> OwnCertificate:
        """Validate and store a chain; deliver it if in use.

        Without ``key`` the chain must belong to the pending key (which then becomes the
        served one) or to the key already served. With ``key`` (a migration of an existing
        certificate) the pair must match; the key goes straight to the volume.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_certificate_unreadable``, ``tls_key_mismatch``, ``tls_name_mismatch``,
                ``tls_certificate_expired``, ``tls_certificate_not_yet_valid``,
                ``tls_volume_unwritable``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        try:
            self._require_applicable()
            if len(chain) > MAX_PEM_BYTES or (key is not None and len(key) > MAX_PEM_BYTES):
                raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
            parsed = self._reader.read_chain(chain)
            validate(parsed, self._host(), now)
            events = [UPLOAD_EVENT]
            if key:
                imported = self._reader.read_key(key)
                if imported.public_key != parsed.public_key:
                    raise DomainError(ErrorCode.TLS_KEY_MISMATCH, "key")
                self._volume.import_key(imported.key_pem)
                events.append(IMPORT_EVENT)
            else:
                keys = self._volume.keys()
                if parsed.public_key == keys.pending:
                    self._volume.promote_pending()
                elif parsed.public_key != keys.current:
                    raise DomainError(ErrorCode.TLS_KEY_MISMATCH, "chain")
            stored = OwnCertificate(
                parsed.certificate, parsed.fingerprint, parsed.chain_pem, parsed.public_key, now
            )
            async with self._uow.locked() as tx:
                await tx.save_own_certificate(stored)
                for event in events:
                    await _audit(tx, now, principal.subject, event, stored)
        except DomainError as error:
            await self._refused(principal, error, UPLOAD_EVENT)
            raise
        if self._source() == OWN:
            self._volume.publish(stored.chain_pem, stored.fingerprint)
        return stored

    async def remove(self, principal: Principal) -> bool:
        """Remove the stored chain; refused while the source is ``own``. The key stays.

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
        self._volume.withdraw()
        return True

    async def choose(self, principal: Principal, raw: str) -> None:
        """Set the source; ``own`` needs a stored chain whose key is on this host.

        Raises:
            DomainError: ``not_permitted``, ``tls_source_not_applicable``,
                ``tls_certificate_missing``, ``tls_key_missing``, and what
                ``SettingsService.change`` raises.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        chosen = raw.strip().lower()
        try:
            self._require_applicable()
            if chosen == OWN:
                status = await self.status()
                if status.uploaded is None:
                    raise DomainError(ErrorCode.TLS_CERTIFICATE_MISSING, SOURCE_KEY)
                if status.key_missing:
                    raise DomainError(ErrorCode.TLS_KEY_MISSING, SOURCE_KEY)
        except DomainError as error:
            await self._refused(principal, error, f"set:{SOURCE_KEY}")
            raise
        await self._settings.change(principal, SOURCE_KEY, chosen)
        await self.sync(chosen)

    async def sync(self, source: str | None = None) -> str:
        """Make the volume match the effective source.

        Returns ``published``, ``withdrawn``, or ``key_missing`` (own chosen, chain stored,
        key not on this host: withdrawn, so ACME applies until the key is back). Called at
        start-up and whenever the portal values change, so a reset, a CLI change or an
        environment lock reach the proxy the same way as a change in the portal.
        """
        if (source or self._source()) == OWN:
            status = await self.status()
            if status.usable and status.uploaded is not None:
                self._volume.publish(status.uploaded.chain_pem, status.uploaded.fingerprint)
                return "published"
            if status.key_missing:
                self._volume.withdraw()
                return "key_missing"
        self._volume.withdraw()
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

    def _require_host(self) -> str:
        """Return the host a request is for; raise without the bundled proxy or an https address."""
        self._require_applicable()
        host = self._host()
        if not host:
            raise DomainError(ErrorCode.TLS_SOURCE_NOT_APPLICABLE, SOURCE_KEY)
        return host

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
    tx: Transaction, now: datetime, actor: str, event: str, stored: OwnCertificate | None = None
) -> None:
    """Record a change: the event, and for a chain its fingerprint and expiry date only."""
    detail = event
    if stored is not None:
        detail = f"{event}:{stored.fingerprint}:{stored.certificate.not_after.date().isoformat()}"
    await tx.add_audit_event(
        AuditEvent(now, actor, _PERMISSION.value, Outcome.SUCCESS, detail=detail)
    )
