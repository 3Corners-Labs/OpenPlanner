"""Management tokens for the citizen's change/cancel link (``/b/{token}``), ADR 0011.

Per booking a random salt (32 bytes, URL-safe) is stored. The token is derived from it with
HMAC-SHA256 under a server key that lives only in the environment, so every message can
carry the link again (confirmation, reminder, change) without storing the token. The
database keeps the salt and the token's SHA-256; a database copy alone cannot produce a
link. Comparison uses ``hmac.compare_digest``.
"""

import base64
import hashlib
import hmac
import secrets

SALT_BYTES = 32
MIN_KEY_BYTES = 32
_PURPOSE = b"manage:"


def new_token_salt() -> str:
    """Return a fresh random salt for a booking."""
    return secrets.token_urlsafe(SALT_BYTES)


def derive_token(key: bytes, salt: str) -> str:
    """Return the management token for ``salt`` under the server ``key``.

    Raises:
        ValueError: If ``key`` is shorter than ``MIN_KEY_BYTES``.
    """
    if len(key) < MIN_KEY_BYTES:
        raise ValueError(f"the token key must be at least {MIN_KEY_BYTES} bytes")
    digest = hmac.new(key, _PURPOSE + salt.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of ``token``, the form used for lookups."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    """Return whether ``token`` hashes to ``token_hash``, in constant time."""
    return hmac.compare_digest(hash_token(token), token_hash)
