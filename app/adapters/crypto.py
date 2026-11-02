"""Encryption of integration secrets at rest (ADR 0008, 0094): AES-256-GCM, named records.

The bootstrap value (``SETTINGS_ENCRYPTION_KEY``) is stretched with HKDF-SHA256 into the AES
key. Every record is sealed with a fresh 96-bit nonce and its name as associated data, so a
ciphertext cannot be moved to another record. Plaintexts never leave this module in logs.

Key rotation (ADR 0094 point 2): a sealed value carries the id of its key, the first 8 bytes of
SHA-256 over the derived AES key, appended to the stored nonce (so a nonce of 12 bytes is a
value from before key ids, and one of 20 bytes carries its id). Opening picks the key by id from
the current key and the previous keys, so a value stays readable while
``SETTINGS_ENCRYPTION_KEY_PREVIOUS`` is set. A value without an id is tried under every key.
"""

import hashlib
import os
from collections.abc import Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12
KEY_ID_BYTES = 8
HKDF_INFO = b"openplanner integration secrets v1"


def _derive(bootstrap_key: bytes) -> bytes:
    """Return the AES-256 key stretched from a bootstrap value."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(
        bootstrap_key
    )


def _key_id(derived: bytes) -> bytes:
    """Return the id of a derived key: the first bytes of its SHA-256."""
    return hashlib.sha256(derived).digest()[:KEY_ID_BYTES]


class SecretBox:
    """Seals and opens named secrets.

    Args:
        bootstrap_key: The value of ``SETTINGS_ENCRYPTION_KEY`` (at least 32 bytes).
        previous_keys: The values of ``SETTINGS_ENCRYPTION_KEY_PREVIOUS``, still readable
            but never used to seal.
    """

    def __init__(self, bootstrap_key: bytes, previous_keys: Sequence[bytes] = ()) -> None:
        """Derive the AES-256 keys."""
        current = _derive(bootstrap_key)
        self._current_id = _key_id(current)
        self._keys: dict[bytes, AESGCM] = {self._current_id: AESGCM(current)}
        for previous in previous_keys:
            derived = _derive(previous)
            self._keys.setdefault(_key_id(derived), AESGCM(derived))

    def seal(self, name: str, plaintext: bytes) -> tuple[bytes, bytes]:
        """Return ``(nonce, ciphertext)`` for ``plaintext`` stored under ``name``.

        The returned nonce is the 12-byte AES-GCM nonce followed by the key id.
        """
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._keys[self._current_id].encrypt(nonce, plaintext, name.encode())
        return nonce + self._current_id, sealed

    def open(self, name: str, nonce: bytes, ciphertext: bytes) -> bytes:
        """Return the plaintext; raises ``cryptography.exceptions.InvalidTag`` if it cannot open.

        That covers a tampered value, a wrong name and a key id that no configured key has.
        """
        raw, key_id = nonce[:NONCE_BYTES], nonce[NONCE_BYTES:]
        if key_id:
            aead = self._keys.get(key_id)
            if aead is None:
                raise InvalidTag
            return aead.decrypt(raw, ciphertext, name.encode())
        for aead in self._keys.values():
            try:
                return aead.decrypt(raw, ciphertext, name.encode())
            except InvalidTag:
                continue
        raise InvalidTag

    def is_current(self, nonce: bytes) -> bool:
        """Return whether a stored nonce says its value is sealed under the current key."""
        return nonce[NONCE_BYTES:] == self._current_id
