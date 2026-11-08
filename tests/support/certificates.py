"""Self-signed certificates for the own-certificate (ADR 0070) and TLS probe tests (ADR 0064).

Test material only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.support.domain import NOW


@dataclass(frozen=True)
class Pair:
    """A PEM chain and the matching PEM key."""

    chain: bytes
    key: bytes


def make_pair(
    host: str = "afspraak.example.nl",
    *,
    not_before: datetime = NOW - timedelta(days=1),
    not_after: datetime = NOW + timedelta(days=90),
    organisation: str = "Acme Demo",
) -> Pair:
    """Return a self-signed EC certificate for ``host`` and its key, both PEM."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation),
            x509.NameAttribute(NameOID.COMMON_NAME, host),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return Pair(
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def self_signed(tmp_path: Path, *, days: int = 60) -> tuple[Path, Path]:
    """Write a self-signed certificate for ``localhost`` and its key; return their paths.

    It is valid from a day ago until ``days`` days from now on the real clock, as a TLS server
    started in the test presents it.
    """
    now = datetime.now(UTC)
    pair = make_pair(
        "localhost",
        not_before=now - timedelta(days=1),
        not_after=now + timedelta(days=days),
        organisation="OpenPlanner test",
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(pair.chain)
    key_path.write_bytes(pair.key)
    return cert_path, key_path
