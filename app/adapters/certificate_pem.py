"""``PemReader`` and key generation on ``cryptography`` (ADR 0066).

A chain is read certificate by certificate and written back in canonical PEM (comments and
stray bytes dropped, the server certificate first as uploaded); an imported key is accepted in
PKCS#8 or the traditional RSA/EC forms, unencrypted, and written back as PKCS#8. Keys are
matched by their public key (SubjectPublicKeyInfo, DER). The leaf's public data comes from the
same parser the probe uses, so the page shows an upload exactly as it shows a served
certificate. A new key for a signing request is ECDSA P-256 (ADR 0066 decision 2).
"""

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

from app.adapters.tls_probe import parse_certificate
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import ParsedChain, ParsedKey

PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey

_SPKI = serialization.PublicFormat.SubjectPublicKeyInfo
_PEM = serialization.Encoding.PEM


class CryptographyPemReader:
    """Parses what the superuser uploaded; never keeps or logs any of it."""

    def read_chain(self, chain: bytes) -> ParsedChain:
        """Return the chain, normalised, with the leaf's public data, fingerprint and key.

        Raises:
            DomainError: ``tls_certificate_unreadable`` (field ``chain``) when the file is
                not PEM or holds no certificate.
        """
        try:
            certificates = x509.load_pem_x509_certificates(chain)
        except ValueError as exc:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain") from exc
        if not certificates:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
        leaf = certificates[0]
        return ParsedChain(
            parse_certificate(leaf.public_bytes(serialization.Encoding.DER)),
            leaf.fingerprint(hashes.SHA256()).hex(),
            "".join(c.public_bytes(_PEM).decode() for c in certificates),
            leaf.public_key().public_bytes(serialization.Encoding.DER, _SPKI),
        )

    def read_key(self, key: bytes) -> ParsedKey:
        """Return the key as PKCS#8 with its public key.

        Raises:
            DomainError: ``tls_certificate_unreadable`` (field ``key``) when the file is not
                an unencrypted private key of a type the proxy serves.
        """
        private = load_key(key)
        return ParsedKey(key_pem(private), public_key(private))


def load_key(key: bytes) -> PrivateKey:
    """Load an unencrypted private key of a type Caddy serves.

    Raises:
        DomainError: ``tls_certificate_unreadable`` (field ``key``).
    """
    try:
        loaded = serialization.load_pem_private_key(key, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        # TypeError: the key is encrypted and no password was given.
        raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "key") from exc
    if not isinstance(
        loaded, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey
    ):
        raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "key")
    return loaded


def public_key(private: PrivateKey) -> bytes:
    """Return the public key of ``private`` as SubjectPublicKeyInfo (DER)."""
    return private.public_key().public_bytes(serialization.Encoding.DER, _SPKI)


def key_pem(private: PrivateKey) -> str:
    """Write ``private`` as unencrypted PKCS#8 PEM."""
    return private.private_bytes(
        _PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def new_key_and_request(host: str) -> tuple[str, str]:
    """Make an ECDSA P-256 key and a signing request for ``host``; return both as PEM."""
    private = ec.generate_private_key(ec.SECP256R1())
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(private, hashes.SHA256())
    )
    return key_pem(private), request.public_bytes(_PEM).decode()
