"""Key pairs for ``private_key_jwt`` (ADR 0083 point 6): EC P-256, one per provider.

The ``SigningKeys`` port. The key id is the JWK thumbprint (RFC 7638), so it is derived from
the key itself and never collides. The private key leaves this module once, as PEM, on its
way to the sealed column; the public JWK is what the JWKS publishes.
"""

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.domain.identity_providers import GeneratedKey

CURVE = ec.SECP256R1()
"""P-256, the curve of ``ES256``; fixed by the ADR."""

ALGORITHM = "ES256"


def _b64url(raw: bytes) -> str:
    """Return the base64url encoding without padding (RFC 7515 §2)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _coordinate(value: int) -> str:
    """Return a P-256 coordinate as 32 big-endian bytes, base64url."""
    return _b64url(value.to_bytes(32, "big"))


def thumbprint(jwk: dict[str, str]) -> str:
    """Return the RFC 7638 thumbprint of an EC JWK (its required members, in order)."""
    required = {name: jwk[name] for name in ("crv", "kty", "x", "y")}
    digest = hashlib.sha256(json.dumps(required, separators=(",", ":")).encode()).digest()
    return _b64url(digest)


class EcSigningKeys:
    """``SigningKeys`` with ``cryptography``."""

    def generate(self) -> GeneratedKey:
        """Return a fresh P-256 key pair, its public JWK and thumbprint key id."""
        private = ec.generate_private_key(CURVE)
        numbers = private.public_key().public_numbers()
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": _coordinate(numbers.x),
            "y": _coordinate(numbers.y),
            "use": "sig",
            "alg": ALGORITHM,
        }
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        return GeneratedKey(thumbprint(jwk), jwk, pem)
