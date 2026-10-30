"""The own certificate of the bundled proxy: what is uploaded and the rules it must meet (ADR 0066).

Pure data, shared by the ports (``PemReader``, ``CertificateVolume``, the stored record), the
service (``app.domain.tls_source``) and the page. The private key never enters the domain as
a stored value: it is generated on, or imported straight into, the volume the proxy reads
(``CertificateVolume``), and the domain only compares public keys (opaque bytes) to tell
whether a chain belongs to the key on this host. The one field that carries a key's text
(``ParsedKey.key_pem``, on its way from an upload to the volume) is excluded from ``repr`` so
no traceback or log line can carry it.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.errors import DomainError, ErrorCode
from app.domain.tls import Certificate

SOURCE_KEY = "tls_certificate_source"
"""The platform setting that chooses the source."""

ACME = "acme"
OWN = "own"
SOURCES = (ACME, OWN)

MAX_PEM_BYTES = 64 * 1024
"""Largest chain or key file accepted; fixed (ADR 0066, *Fixed on purpose*)."""

UPLOAD_EVENT = "tls.certificate.upload"
REMOVE_EVENT = "tls.certificate.remove"
GENERATE_EVENT = "tls.key.generate"
IMPORT_EVENT = "tls.key.import"


@dataclass(frozen=True)
class ParsedChain:
    """An uploaded chain as the reader parsed it.

    Attributes:
        certificate: The leaf certificate's public data.
        fingerprint: SHA-256 of the leaf certificate, lower-case hex.
        chain_pem: The chain, normalised (server certificate first).
        public_key: The leaf's public key (SubjectPublicKeyInfo, DER), to match a key.
    """

    certificate: Certificate
    fingerprint: str
    chain_pem: str = field(repr=False)
    public_key: bytes = field(repr=False, default=b"")


@dataclass(frozen=True)
class ParsedKey:
    """An imported private key as the reader parsed it; on its way to the volume only.

    Attributes:
        key_pem: The key, normalised (PKCS#8); never logged, never repr'd.
        public_key: Its public key (SubjectPublicKeyInfo, DER), to match the chain.
    """

    key_pem: str = field(repr=False)
    public_key: bytes = field(repr=False, default=b"")


@dataclass(frozen=True)
class HostKeys:
    """What the volume on this host holds: public keys only, and the pending CSR.

    Attributes:
        current: Public key of the key the proxy serves with (``privkey.pem``), if any.
        pending: Public key of a generated key waiting for its certificate, if any.
        pending_csr: The certificate signing request for that key (PEM), if any.
    """

    current: bytes | None = None
    pending: bytes | None = None
    pending_csr: str | None = None


@dataclass(frozen=True)
class OwnCertificate:
    """The stored own certificate: its public data and chain; never a key.

    Attributes:
        certificate: The leaf certificate's public data.
        fingerprint: SHA-256 of the leaf certificate, lower-case hex.
        chain_pem: The chain as the proxy will serve it.
        public_key: The leaf's public key, to tell whether the key is on this host.
        uploaded_at: When the chain was stored.
    """

    certificate: Certificate
    fingerprint: str
    chain_pem: str = field(repr=False)
    public_key: bytes = field(repr=False, default=b"")
    uploaded_at: datetime = datetime.min

    def fingerprint_text(self) -> str:
        """Return the fingerprint as ``AB:CD:…``, the way browsers show it."""
        return fingerprint_text(self.fingerprint)


@dataclass(frozen=True)
class SourceStatus:
    """The certificate source as the page shows it (ADR 0066 decision 4).

    Attributes:
        uploaded: The stored chain, if any.
        key_on_host: Whether the key of the stored chain is on this host; ``False`` after
            a database restore elsewhere, which the page reports as a finding.
        pending_csr: A generated request waiting for its certificate, if any.
    """

    uploaded: OwnCertificate | None
    key_on_host: bool
    pending_csr: str | None

    @property
    def key_missing(self) -> bool:
        """Whether a chain is stored whose key this host does not have."""
        return self.uploaded is not None and not self.key_on_host

    @property
    def usable(self) -> bool:
        """Whether the own certificate can be served from this host."""
        return self.uploaded is not None and self.key_on_host


def fingerprint_text(fingerprint: str) -> str:
    """Write a hex fingerprint as upper-case pairs separated by colons."""
    upper = fingerprint.upper()
    return ":".join(upper[i : i + 2] for i in range(0, len(upper), 2))


def validate(chain: ParsedChain, host: str | None, now: datetime) -> None:
    """Refuse a chain the proxy could not serve for ``host``.

    Raises:
        DomainError: ``tls_certificate_expired``, ``tls_certificate_not_yet_valid`` or
            ``tls_name_mismatch``, in that order.
    """
    certificate = chain.certificate
    if now >= certificate.not_after:
        raise DomainError(ErrorCode.TLS_CERTIFICATE_EXPIRED, "chain")
    if now < certificate.not_before:
        raise DomainError(ErrorCode.TLS_CERTIFICATE_NOT_YET_VALID, "chain")
    if host and not certificate.covers(host):
        raise DomainError(ErrorCode.TLS_NAME_MISMATCH, "chain")
