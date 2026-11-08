"""Values in a configuration document (ADR 0099): JSON, sealed and opened secrets, binaries.

A document is JSON, and two kinds of value need more than JSON can say. A *secret* is sealed
in the document (``Sealed``) and opened in memory (``Secret``), which never prints, never
reaches a plan or a log, and is compared by a keyed digest so two equal secrets are not a
change. A *binary* (a logo, a font, a closure image) carries its media type and its bytes, and
knows its SHA-256. This module is also the one home of the canonical JSON form that the plan
hash and the document digest are made from (ADR 0099 point 6).
"""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
"""What JSON can say: the values of a document that are not secrets or binaries."""

_DIGEST_KEY = secrets.token_bytes(32)
"""Keys the digest of an opened secret for this process only, so a digest that leaks into a
dump cannot be used to guess a weak secret offline, and never matches one from another run."""

SEALED_PLACEHOLDER = "<sealed>"
SECRET_PLACEHOLDER = "<secret>"  # noqa: S105  # nosec B105 - a placeholder, not a password
BINARY_PLACEHOLDER = "<binary {media_type}>"
"""What ``redact`` shows in place of a value that must not be shown."""


@dataclass(frozen=True)
class Sealed:
    """A secret as a document carries it, still sealed under the passphrase key.

    Attributes:
        token: Base64 of the sealed value (ADR 0099 point 8); the adapter opens it with the
            object's natural key and the field name as associated data.
    """

    token: str


class Secret:
    """A secret opened in memory: never printed, never in a plan, compared by digest.

    Args:
        plaintext: The secret. It is only readable through ``reveal``.
    """

    __slots__ = ("_digest", "_plaintext")

    def __init__(self, plaintext: str) -> None:
        """Keep the secret and its keyed digest."""
        self._plaintext = plaintext
        self._digest = hmac.new(_DIGEST_KEY, plaintext.encode(), hashlib.sha256).hexdigest()

    @property
    def digest(self) -> str:
        """A keyed digest of the secret: equal for equal secrets within this process only."""
        return self._digest

    def reveal(self) -> str:
        """Return the secret; the one place that does, for the adapter that seals it again."""
        return self._plaintext

    def __eq__(self, other: object) -> bool:
        """Two secrets are equal when their digests are; anything else is not a secret."""
        return isinstance(other, Secret) and hmac.compare_digest(self._digest, other._digest)

    def __hash__(self) -> int:
        """Hash the digest, so a secret works as a key and in a set without being exposed."""
        return hash(self._digest)

    def __repr__(self) -> str:
        """Show that there is a secret, never which."""
        return "Secret(…)"

    __str__ = __repr__


@dataclass(frozen=True)
class Binary:
    """An image or a font of a document: a media type and its bytes.

    Attributes:
        media_type: The media type, validated by the structure checks of ADR 0092 on apply.
        data: The bytes. They are left out of ``repr`` so a log line never holds a file.
    """

    media_type: str
    data: bytes = field(repr=False)

    @property
    def sha256(self) -> str:
        """The lower-case hex SHA-256 of the bytes, as a document states it."""
        return hashlib.sha256(self.data).hexdigest()


type Value = Json | Sealed | Secret | Binary
"""A field value in a document or in the installation's current configuration."""


def canonical_json(value: Json) -> str:
    """Return the canonical JSON text of ``value``: sorted keys, no spaces, UTF-8 characters.

    The plan hash and the document digest are made from this form, so the same content hashes
    the same however it was written (ADR 0099 point 6). It refuses NaN and infinity.

    Raises:
        ValueError: ``value`` holds a number JSON cannot represent.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def document_digest(source: Json) -> str:
    """Return the SHA-256 (lower-case hex) of the canonical form of a document's content."""
    return hashlib.sha256(canonical_json(source).encode()).hexdigest()


def redact(value: object) -> object:
    """Return ``value`` with every secret, sealed value and binary replaced by a placeholder.

    For anything that shows values to a person or a log: a plan lists names of changed fields,
    and a place that does show a value shows this form of it.
    """
    if isinstance(value, Secret):
        return SECRET_PLACEHOLDER
    if isinstance(value, Sealed):
        return SEALED_PLACEHOLDER
    if isinstance(value, Binary):
        return BINARY_PLACEHOLDER.format(media_type=value.media_type)
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value
