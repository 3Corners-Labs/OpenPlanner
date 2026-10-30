"""``PemReader`` on ``cryptography``: parses an uploaded chain and private key (ADR 0066).

The chain is read certificate by certificate and written back in canonical PEM (comments and
stray bytes dropped, the server certificate first as uploaded); the key is accepted in PKCS#8
or the traditional RSA/EC forms, unencrypted, and written back as PKCS#8. Whether the key fits
is decided by comparing the public keys. The leaf's public data comes from the same parser the
probe uses, so the page shows an upload exactly as it shows a served certificate.
"""

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from app.adapters.tls_probe import parse_certificate
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import PemPair

PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey

_SPKI = serialization.PublicFormat.SubjectPublicKeyInfo
_PEM = serialization.Encoding.PEM


class PemPairReader:
    """Parses what the superuser uploaded; never keeps or logs any of it."""

    def read(self, chain: bytes, key: bytes) -> PemPair:
        """Return the pair, normalised, with the leaf's public data and fingerprint.

        Raises:
            DomainError: ``tls_certificate_unreadable`` (field ``chain`` or ``key``) when a
                file is not PEM, holds no certificate, or the key is encrypted or of an
                unsupported type.
        """
        try:
            certificates = x509.load_pem_x509_certificates(chain)
        except ValueError as exc:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain") from exc
        if not certificates:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
        private = _load_key(key)
        leaf = certificates[0]
        matches = leaf.public_key().public_bytes(_PEM, _SPKI) == private.public_key().public_bytes(
            _PEM, _SPKI
        )
        return PemPair(
            parse_certificate(leaf.public_bytes(serialization.Encoding.DER)),
            leaf.fingerprint(hashes.SHA256()).hex(),
            "".join(c.public_bytes(_PEM).decode() for c in certificates),
            private.private_bytes(
                _PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ).decode(),
            matches,
        )


def _load_key(key: bytes) -> PrivateKey:
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
