"""Decoding and encoding a configuration document as JSON text (ADR 0099 point 10).

The domain reads a decoded JSON object; this module is the one place that turns bytes into one
and back. It refuses what would make a document ambiguous or dangerous: more bytes than the
limit, text that is not UTF-8 or not JSON, an object with a repeated key (the last would win
silently), NaN and infinity, and nesting deeper than the format ever is.
"""

import json
from typing import Any

from app.domain.configuration_values import Json
from app.domain.errors import DomainError, ErrorCode

MEDIA_TYPE = "application/vnd.openplanner.config+json"
"""The media type of a document (ADR 0099 point 1)."""

MAX_DEPTH = 32
"""The deepest nesting accepted; the format itself is about eight levels deep."""


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an object from its pairs, refusing a key that occurs twice."""
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _refuse_constant(name: str) -> Any:
    """Refuse NaN and the infinities, which JSON cannot say."""
    raise ValueError(name)


def _finite(text: str) -> float:
    """Read a number with a fraction or exponent, refusing what overflows to infinity."""
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(text)
    return value


def _depth(value: Any, level: int = 0) -> int:
    """Return how deeply ``value`` nests, stopping once it is deeper than allowed."""
    if level > MAX_DEPTH:
        return level
    if isinstance(value, dict):
        return max((_depth(item, level + 1) for item in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level)
    return level


def decode(data: bytes, max_bytes: int) -> object:
    """Return the JSON a document is written in.

    Raises:
        DomainError: ``configuration_package_too_large`` above ``max_bytes``;
            ``configuration_package_invalid`` for anything that is not one JSON value.
    """
    if len(data) > max_bytes:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "document")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_refuse_constant,
            parse_float=_finite,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "document") from None
    if _depth(value) > MAX_DEPTH:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "document")
    return value


def encode(document: Json) -> bytes:
    """Return the document as readable UTF-8 JSON text, indented, ending in a newline."""
    return (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


class JsonCodec:
    """The ``DocumentCodec`` port on JSON text."""

    def decode(self, data: bytes, max_bytes: int) -> object:
        """Return the JSON in ``data``; refuse what is not one document."""
        return decode(data, max_bytes)

    def encode(self, document: Json) -> bytes:
        """Return the text of a document."""
        return encode(document)
