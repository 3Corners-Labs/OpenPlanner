"""``CertificateProbe`` on the standard library's ``ssl`` (ADR 0064).

Two handshakes at most: one verified against the host's CA bundle (``check_hostname`` on), which
settles the trust verdict; when it fails, one without verification only to read the certificate
the server presents, so the status page can still say who issued it and when it expires. The
certificate is parsed with ``cryptography`` (already present for PyJWT). Nothing is sent but
the TLS client hello; no request reaches the application behind the proxy.
"""

import asyncio
import ssl
from contextlib import suppress

import structlog
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

from app.domain.tls import Certificate, ProbeResult, Trust

log = structlog.get_logger()

TIMEOUT_SECONDS = 10.0
"""How long a connection and handshake may take; fixed (ADR 0064, *Fixed on purpose*)."""

# OpenSSL verification codes (X509_V_ERR_*), as ``ssl.SSLCertVerificationError.verify_code``.
_NOT_YET_VALID = 9
_EXPIRED = 10
_HOSTNAME_MISMATCH = 62
_IP_ADDRESS_MISMATCH = 64


class SslCertificateProbe:
    """Connects to a server, verifies its chain and reads its leaf certificate.

    Args:
        timeout: Seconds for connection plus handshake.
        cafile: A CA bundle to trust instead of the host's (tests, a private CA).
    """

    def __init__(self, timeout: float = TIMEOUT_SECONDS, cafile: str | None = None) -> None:
        """Store the options."""
        self._timeout = timeout
        self._cafile = cafile

    async def probe(self, host: str, port: int) -> ProbeResult:
        """Return the trust verdict and the certificate; never raises."""
        try:
            trust, der = await asyncio.wait_for(self._verified(host, port), self._timeout)
        except TimeoutError:
            return ProbeResult(error="timeout")
        except ssl.SSLError as exc:
            log.warning("tls.handshake_failed", host=host, error=type(exc).__name__)
            return ProbeResult(error="handshake_failed")
        except OSError as exc:
            log.warning("tls.unreachable", host=host, error=type(exc).__name__)
            return ProbeResult(error="unreachable")
        if der is None:
            try:
                der = await asyncio.wait_for(self._unverified(host, port), self._timeout)
            except (TimeoutError, OSError):
                return ProbeResult(trust=trust, error="handshake_failed")
        try:
            certificate = parse_certificate(der)
        except ValueError:
            return ProbeResult(trust=trust, error="unreadable")
        return ProbeResult(certificate, trust)

    async def _verified(self, host: str, port: int) -> tuple[Trust, bytes | None]:
        """Handshake with verification; a verification failure is a verdict, not an error."""
        context = ssl.create_default_context(cafile=self._cafile)
        try:
            return Trust.TRUSTED, await _handshake(host, port, context)
        except ssl.SSLCertVerificationError as exc:
            return _trust(exc.verify_code), None

    async def _unverified(self, host: str, port: int) -> bytes:
        """Handshake without verification, only to read what the server presents."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        # The verified handshake above already settled the trust verdict; this one reads the
        # certificate of a server the host does not trust, and sends nothing after the hello.
        context.verify_mode = ssl.CERT_NONE
        return await _handshake(host, port, context)


async def _handshake(host: str, port: int, context: ssl.SSLContext) -> bytes:
    """Open a TLS connection, return the peer's DER certificate and close."""
    _, writer = await asyncio.open_connection(host, port, ssl=context, server_hostname=host)
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        der: bytes = ssl_object.getpeercert(binary_form=True)
        return der
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


def _trust(verify_code: int) -> Trust:
    """Map OpenSSL's verification failure to a verdict."""
    if verify_code in (_HOSTNAME_MISMATCH, _IP_ADDRESS_MISMATCH):
        return Trust.NAME_MISMATCH
    if verify_code == _EXPIRED:
        return Trust.EXPIRED
    if verify_code == _NOT_YET_VALID:
        return Trust.NOT_YET_VALID
    return Trust.UNTRUSTED


def parse_certificate(der: bytes) -> Certificate:
    """Read subject, issuer, validity and DNS names from a DER certificate.

    Raises:
        ValueError: The bytes are not a certificate.
    """
    certificate = x509.load_der_x509_certificate(der)
    try:
        extension = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        alternative = extension.value
        names = tuple(alternative.get_values_for_type(x509.DNSName))  # type: ignore[attr-defined]
    except x509.ExtensionNotFound:
        names = ()
    return Certificate(
        _name(certificate.subject),
        _name(certificate.issuer),
        certificate.not_valid_before_utc,
        certificate.not_valid_after_utc,
        names,
    )


NAME_LENGTH = 200
"""Longest subject or issuer kept: the server chooses these strings, the column is 255 wide."""


def _name(name: x509.Name) -> str:
    """Write a name as ``Organisation (common name)``, or whichever of the two it has."""
    common = next((str(a.value) for a in name.get_attributes_for_oid(NameOID.COMMON_NAME)), "")
    organisation = next(
        (str(a.value) for a in name.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)), ""
    )
    if common and organisation and common != organisation:
        written = f"{organisation} ({common})"
    else:
        written = common or organisation or name.rfc4514_string()
    return written[:NAME_LENGTH]
