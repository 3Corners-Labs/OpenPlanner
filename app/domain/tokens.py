"""Management tokens for the participant's change/cancel link (``/b/{token}``), ADR 0011.

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
from collections.abc import Sequence

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


def token_for_booking(keys: Sequence[bytes], salt: str, token_hash: str) -> str:
    """Return the token of a booking: under the first key whose token matches ``token_hash``.

    ``keys`` is the current key, then the previous one while a rotation runs (ADR 0094 point
    4), so a message sent after the rotation still carries the link the booking was given.
    A booking that matches no key gets the token under the first key (a dead link, as before).

    Raises:
        ValueError: If a key is shorter than ``MIN_KEY_BYTES``.
    """
    tokens = [derive_token(key, salt) for key in keys]
    for token in tokens:
        if token_matches(token, token_hash):
            return token
    return tokens[0]


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of ``token``, the form used for lookups."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    """Return whether ``token`` hashes to ``token_hash``, in constant time."""
    return hmac.compare_digest(hash_token(token), token_hash)


def agent_pseudonyms(keys: Sequence[bytes], subject: str) -> tuple[str, ...]:
    """Return the pseudonyms of ``subject`` under the current key and the previous one, if any."""
    return tuple(agent_pseudonym(key, subject) for key in keys)


def agent_pseudonym(key: bytes, subject: str) -> str:
    """Return the pseudonym that records which agent claimed a booking (ADR 0043).

    HMAC-SHA256 of the identity provider's subject under ``key`` (``keys.agent_pseudonym_key``,
    never the token key itself), as hex. The same agent always gets the same pseudonym, so
    "claimed by me" can be recognised, but the subject cannot be read back from it.
    """
    return hmac.new(key, subject.encode(), hashlib.sha256).hexdigest()
