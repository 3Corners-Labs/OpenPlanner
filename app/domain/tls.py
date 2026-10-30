"""Certificates of the public address: what a probe sees and how it is judged (ADR 0064).

Pure data and rules, shared by the ports (``CertificateProbe``, the stored check), the
worker's watch (``app.domain.tls_check``), the status page and the CLI. The judgement is one
word (``Finding``), so the status page, the system event and the CLI say the same thing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

EVENT = "tls.certificate"
"""Action of the system event about the certificate."""

HTTPS_PORT = 443
"""The port probed when the public address names none."""


class Trust(StrEnum):
    """What the verified handshake against the host's CA bundle said about the chain."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    NAME_MISMATCH = "name_mismatch"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"


class Finding(StrEnum):
    """The one-word judgement of a check, shown on System status and in the system event."""

    OK = "ok"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    UNTRUSTED = "untrusted"
    NAME_MISMATCH = "name_mismatch"
    UNREACHABLE = "unreachable"
    NOT_HTTPS = "not_https"


@dataclass(frozen=True)
class Certificate:
    """The leaf certificate a server presented; public data only.

    Attributes:
        subject: Its common name (or the whole subject when it has none).
        issuer: The issuing CA's name, e.g. ``Let's Encrypt (R11)``.
        not_before: Start of validity (UTC).
        not_after: End of validity (UTC).
        names: The DNS names it is valid for.
    """

    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    names: tuple[str, ...] = ()

    def days_left(self, now: datetime) -> int:
        """Return the whole days until ``not_after`` (negative once expired)."""
        return (self.not_after - now) // timedelta(days=1)

    def covers(self, host: str) -> bool:
        """Return whether ``host`` is one of the names (one-label wildcards included)."""
        wanted = host.lower().rstrip(".")
        for name in self.names:
            candidate = name.lower()
            if candidate == wanted:
                return True
            if candidate.startswith("*.") and wanted.count(".") >= 1:
                if wanted.split(".", 1)[1] == candidate[2:]:
                    return True
        return False


@dataclass(frozen=True)
class ProbeResult:
    """What a probe found: a certificate with its trust, or why there was none.

    Attributes:
        certificate: The leaf certificate; ``None`` without a completed handshake.
        trust: The verdict of the verified handshake; ``None`` without one.
        error: Why no handshake completed: ``unreachable``, ``timeout``, ``handshake_failed``.
    """

    certificate: Certificate | None = None
    trust: Trust | None = None
    error: str = ""


@dataclass(frozen=True)
class CertificateCheck:
    """The stored outcome of the latest check.

    Attributes:
        checked_at: When the probe ran.
        host: The host name probed.
        finding: The judgement.
        certificate: The certificate seen, if any.
        trust: The handshake's verdict, if any.
        error: The probe's error code, if any.
    """

    checked_at: datetime
    host: str
    finding: Finding
    certificate: Certificate | None = None
    trust: Trust | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether nothing needs attention."""
        return self.finding is Finding.OK

    def days_left(self, now: datetime) -> int | None:
        """Return the days the certificate is still valid, if one was seen."""
        return None if self.certificate is None else self.certificate.days_left(now)


@dataclass(frozen=True)
class Address:
    """The host and port of a public address to probe."""

    host: str
    port: int = HTTPS_PORT


def public_address(public_base_url: str) -> Address | None:
    """Return the host and port of an ``https`` address; ``None`` for anything else."""
    parts = urlsplit(public_base_url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return Address(parts.hostname, parts.port or HTTPS_PORT)


def judge(result: ProbeResult, host: str, now: datetime, warn_within: timedelta) -> Finding:
    """Return the finding for a probe's result.

    The handshake's verdict wins over anything read from the certificate, so a chain the host
    does not trust is ``untrusted`` even when the dates are fine; a trusted certificate that
    expires within ``warn_within`` is ``expiring``.
    """
    if result.certificate is None or result.trust is None:
        return Finding.UNREACHABLE
    if result.trust is not Trust.TRUSTED:
        return Finding(result.trust.value)
    certificate = result.certificate
    if now >= certificate.not_after:
        return Finding.EXPIRED
    if now < certificate.not_before:
        return Finding.NOT_YET_VALID
    if not certificate.covers(host):
        return Finding.NAME_MISMATCH
    if certificate.not_after - now < warn_within:
        return Finding.EXPIRING
    return Finding.OK
