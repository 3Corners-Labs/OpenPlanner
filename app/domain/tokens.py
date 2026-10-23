"""Management tokens for the citizen's change/cancel link (``/b/{token}``).

A token is 32 random bytes, URL-safe. Only its SHA-256 is stored; the token itself appears
once, in the confirmation email. Looking up by hash reveals nothing about the token, and the
final comparison uses ``hmac.compare_digest``.
"""

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of ``token``, the only form that is stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_management_token() -> tuple[str, str]:
    """Return a fresh ``(token, token_hash)`` pair."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def token_matches(token: str, token_hash: str) -> bool:
    """Return whether ``token`` hashes to ``token_hash``, in constant time."""
    return hmac.compare_digest(hash_token(token), token_hash)
