"""Encryption of integration secrets at rest (ADR 0008): AES-256-GCM, one key, named records.

The bootstrap value (``SETTINGS_ENCRYPTION_KEY``) is stretched with HKDF-SHA256 into the AES
key. Every record is sealed with a fresh 96-bit nonce and its name as associated data, so a
ciphertext cannot be moved to another record. Plaintexts never leave this module in logs.
"""

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12
HKDF_INFO = b"openplanner integration secrets v1"


class SecretBox:
    """Seals and opens named secrets.

    Args:
        bootstrap_key: The value of ``SETTINGS_ENCRYPTION_KEY`` (at least 32 bytes).
    """

    def __init__(self, bootstrap_key: bytes) -> None:
        """Derive the AES-256 key."""
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(
            bootstrap_key
        )
        self._aead = AESGCM(key)

    def seal(self, name: str, plaintext: bytes) -> tuple[bytes, bytes]:
        """Return ``(nonce, ciphertext)`` for ``plaintext`` stored under ``name``."""
        nonce = os.urandom(NONCE_BYTES)
        return nonce, self._aead.encrypt(nonce, plaintext, name.encode())

    def open(self, name: str, nonce: bytes, ciphertext: bytes) -> bytes:
        """Return the plaintext; raises ``cryptography.exceptions.InvalidTag`` if tampered."""
        return self._aead.decrypt(nonce, ciphertext, name.encode())
