"""The own certificate of the bundled proxy: what is uploaded and the rules it must meet (ADR 0066).

Pure data, shared by the port (``PemReader``, the stored record), the service
(``app.domain.tls_source``) and the page. The private key appears only as a field the reader
hands to the service once (``PemPair.key_pem``); it is excluded from ``repr`` so no traceback or
log line can carry it.
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


@dataclass(frozen=True)
class PemPair:
    """An uploaded chain and key as the reader parsed them.

    Attributes:
        certificate: The leaf certificate's public data.
        fingerprint: SHA-256 of the leaf certificate, lower-case hex.
        chain_pem: The chain, normalised (server certificate first).
        key_pem: The private key, normalised; never logged, never repr'd.
        key_matches: Whether the key belongs to the leaf certificate.
    """

    certificate: Certificate
    fingerprint: str
    chain_pem: str = field(repr=False)
    key_pem: str = field(repr=False)
    key_matches: bool = True


@dataclass(frozen=True)
class OwnCertificate:
    """The stored own certificate: its public data and when it was uploaded; never the key.

    Attributes:
        certificate: The leaf certificate's public data.
        fingerprint: SHA-256 of the leaf certificate, lower-case hex.
        chain_pem: The chain as the proxy will serve it.
        uploaded_at: When the pair was stored.
    """

    certificate: Certificate
    fingerprint: str
    chain_pem: str = field(repr=False)
    uploaded_at: datetime = datetime.min

    def fingerprint_text(self) -> str:
        """Return the fingerprint as ``AB:CD:…``, the way browsers show it."""
        return fingerprint_text(self.fingerprint)


def fingerprint_text(fingerprint: str) -> str:
    """Write a hex fingerprint as upper-case pairs separated by colons."""
    upper = fingerprint.upper()
    return ":".join(upper[i : i + 2] for i in range(0, len(upper), 2))


def validate(pair: PemPair, host: str | None, now: datetime) -> None:
    """Refuse a pair the proxy could not serve for ``host``.

    Raises:
        DomainError: ``tls_key_mismatch``, ``tls_certificate_expired``,
            ``tls_certificate_not_yet_valid`` or ``tls_name_mismatch``, in that order.
    """
    if not pair.key_matches:
        raise DomainError(ErrorCode.TLS_KEY_MISMATCH, "key")
    certificate = pair.certificate
    if now >= certificate.not_after:
        raise DomainError(ErrorCode.TLS_CERTIFICATE_EXPIRED, "chain")
    if now < certificate.not_before:
        raise DomainError(ErrorCode.TLS_CERTIFICATE_NOT_YET_VALID, "chain")
    if host and not certificate.covers(host):
        raise DomainError(ErrorCode.TLS_NAME_MISMATCH, "chain")
