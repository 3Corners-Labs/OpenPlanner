"""Uploaded images: recognised from their bytes, stripped of metadata, with a text alternative.

One home for what logos (ADR 0020) and closure images (ADR 0037 §3) share. The type of a file
comes from its magic bytes, never from a file name or a client-supplied type; only PNG, JPEG
and WebP are accepted. SVG is refused: it can carry scripts.

Closure images are stripped of metadata before they are stored, because a photo can carry a
location or a photographer's name. The stripper walks the container format and keeps only the
parts needed to show the picture; anything it cannot walk is refused rather than stored:

    PNG    keep IHDR, PLTE, IDAT, IEND and the colour and animation chunks; drop text (tEXt,
           zTXt, iTXt), eXIf, tIME and anything unknown
    JPEG   drop APP1 (EXIF, XMP), APP3-APP13 (IPTC and the like), APP15 and COM; keep APP0,
           APP2 and APP14 only as JFIF, ICC profile and Adobe colour (not a JFXX thumbnail of
           the uncropped original, not FlashPix data); every other segment stays
    WebP   drop the EXIF and XMP chunks and their flags in VP8X; fix the RIFF size

No imaging library is involved: the image data itself is copied untouched, never decoded.
"""

import hashlib
import re
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.domain.errors import DomainError, ErrorCode

MAX_IMAGE_BYTES = 500_000
"""Largest closure image (ADR 0037 §3); larger uploads are refused."""

MAX_ALT_LENGTH = 250
"""Longest text alternative per locale; a sentence, not a description of the whole page."""

ALT_LOCALES = ("nl", "en")
"""Locales a text alternative must be given in, as for the closure notice."""

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
"""Media types that can be recognised, for the ``accept`` attribute of upload fields."""

_SIGNATURES: tuple[tuple[tuple[tuple[int, bytes], ...], str], ...] = (
    (((0, b"\x89PNG\r\n\x1a\n"),), "image/png"),
    (((0, b"\xff\xd8\xff"),), "image/jpeg"),
    (((0, b"RIFF"), (8, b"WEBP")), "image/webp"),
)
"""Magic bytes at their offsets, and the media type they identify."""

_LINE_BREAKS_OR_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class _MalformedError(Exception):
    """The file does not have the structure its magic bytes promise."""


def recognise(data: bytes) -> str | None:
    """Return the media type identified by the magic bytes of ``data``, or ``None``."""
    for parts, media_type in _SIGNATURES:
        if all(data[at : at + len(magic)] == magic for at, magic in parts):
            return media_type
    return None


def content_digest(data: bytes) -> str:
    """Return the first 16 hex digits of the SHA-256 of ``data``; versions asset URLs."""
    return hashlib.sha256(data).hexdigest()[:16]


def alt_field(locale: str) -> str:
    """Return the form field of the text alternative in ``locale``, e.g. ``alt_nl``."""
    return f"alt_{locale}"


# --- metadata -------------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_KEPT = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"tRNS",
        b"gAMA",
        b"cHRM",
        b"sRGB",
        b"iCCP",
        b"sBIT",
        b"bKGD",
        b"pHYs",
        b"acTL",
        b"fcTL",
        b"fdAT",
    }
)
"""PNG chunks needed to show the picture (also animated); the rest is dropped."""


def _strip_png(data: bytes) -> bytes:
    """Return the PNG with only the chunks of ``_PNG_KEPT``."""
    kept = [_PNG_SIGNATURE]
    at = len(_PNG_SIGNATURE)
    first = True
    while True:
        if at + 12 > len(data):
            raise _MalformedError
        (length,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        end = at + 12 + length
        if end > len(data) or (first and kind != b"IHDR"):
            raise _MalformedError
        if kind in _PNG_KEPT:
            kept.append(data[at:end])
        first = False
        at = end
        if kind == b"IEND":
            return b"".join(kept)


_JPEG_DROPPED = frozenset({0xE1, *range(0xE3, 0xEE), 0xEF, 0xFE})
"""APP1, APP3-APP13, APP15 and COM: the segments that carry metadata."""
_JPEG_IDENTIFIED = {0xE0: b"JFIF\x00", 0xE2: b"ICC_PROFILE\x00", 0xEE: b"Adobe"}
"""APP segments kept only with this identifier at the start of their data."""
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
"""Markers without a length: TEM and the restart markers."""


def _scan_end(data: bytes, at: int) -> int:
    """Return where the entropy-coded data starting at ``at`` ends (at the next marker)."""
    while True:
        at = data.find(b"\xff", at)
        if at < 0 or at + 1 >= len(data):
            raise _MalformedError
        follower = data[at + 1]
        if follower == 0x00 or follower in _JPEG_STANDALONE or follower == 0xFF:
            at += 1 if follower == 0xFF else 2
            continue
        return at


def _strip_jpeg(data: bytes) -> bytes:
    """Return the JPEG without the segments of ``_JPEG_DROPPED``."""
    kept = [data[:2]]
    at = 2
    while True:
        if at + 2 > len(data) or data[at] != 0xFF:
            raise _MalformedError
        marker = data[at + 1]
        if marker == 0xFF:  # fill byte before a marker
            at += 1
            continue
        if marker == 0xD9:  # end of image
            kept.append(data[at : at + 2])
            return b"".join(kept)
        if marker in _JPEG_STANDALONE:
            kept.append(data[at : at + 2])
            at += 2
            continue
        if at + 4 > len(data):
            raise _MalformedError
        (length,) = struct.unpack(">H", data[at + 2 : at + 4])
        end = at + 2 + length
        if length < 2 or end > len(data):
            raise _MalformedError
        if marker == 0xDA:  # start of scan: header, then entropy-coded data up to a marker
            end = _scan_end(data, end)
        identifier = _JPEG_IDENTIFIED.get(marker)
        if marker not in _JPEG_DROPPED and (
            identifier is None or data[at + 4 : at + 4 + len(identifier)] == identifier
        ):
            kept.append(data[at:end])
        at = end


_WEBP_DROPPED = frozenset({b"EXIF", b"XMP "})
_WEBP_IMAGE = frozenset({b"VP8 ", b"VP8L", b"VP8X"})
_VP8X_METADATA_FLAGS = 0x08 | 0x04
"""The EXIF and XMP bits of the VP8X flags byte."""


def _strip_webp(data: bytes) -> bytes:
    """Return the WebP without EXIF and XMP chunks, with VP8X flags and RIFF size fixed."""
    (size,) = struct.unpack("<I", data[4:8])
    if size + 8 != len(data):
        raise _MalformedError
    kept = [b"WEBP"]
    at = 12
    seen_image = False
    while at < len(data):
        if at + 8 > len(data):
            raise _MalformedError
        kind = data[at : at + 4]
        (length,) = struct.unpack("<I", data[at + 4 : at + 8])
        end = at + 8 + length + (length % 2)
        if end > len(data):
            raise _MalformedError
        chunk = data[at:end]
        if kind == b"VP8X" and length >= 1:
            flags = chunk[8] & ~_VP8X_METADATA_FLAGS
            chunk = chunk[:8] + bytes((flags,)) + chunk[9:]
        if kind not in _WEBP_DROPPED:
            kept.append(chunk)
        seen_image = seen_image or kind in _WEBP_IMAGE
        at = end
    if not seen_image:
        raise _MalformedError
    body = b"".join(kept)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_STRIPPERS: Mapping[str, Callable[[bytes], bytes]] = {
    "image/png": _strip_png,
    "image/jpeg": _strip_jpeg,
    "image/webp": _strip_webp,
}


# --- values ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageText:
    """The text alternative of an image (WCAG 1.1.1).

    Attributes:
        alts: The text per locale of ``ALT_LOCALES``, trimmed; empty when decorative.
        decorative: The image adds nothing the text does not say; pages give ``alt=""``.

    Raises:
        DomainError: ``invalid_configuration`` with ``alt_<locale>`` when a text is missing
            (and the image is not decorative), too long, or has a line break or control
            character.
    """

    alts: Mapping[str, str] = field(default_factory=dict)
    decorative: bool = False

    def __post_init__(self) -> None:
        """Trim and validate the texts; a decorative image keeps none."""
        if self.decorative:
            object.__setattr__(self, "alts", {})
            return
        kept: dict[str, str] = {}
        for locale in ALT_LOCALES:
            text = self.alts.get(locale, "").strip()
            if not text or len(text) > MAX_ALT_LENGTH or _LINE_BREAKS_OR_CONTROL.search(text):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, alt_field(locale))
            kept[locale] = text
        object.__setattr__(self, "alts", kept)

    def alt(self, locale: str) -> str:
        """Return the text in ``locale`` (Dutch as fallback); empty when decorative."""
        if self.decorative:
            return ""
        return self.alts.get(locale) or self.alts[ALT_LOCALES[0]]


@dataclass(frozen=True)
class PageImage:
    """What a page needs to show a stored image: type, content digest and text alternative."""

    media_type: str
    digest: str
    text: ImageText


@dataclass(frozen=True)
class ImageFile:
    """A validated image whose metadata has been stripped. Build with ``from_upload``."""

    media_type: str
    data: bytes

    @classmethod
    def from_upload(cls, data: bytes, limit: int = MAX_IMAGE_BYTES) -> "ImageFile":
        """Recognise the type of uploaded bytes and strip their metadata.

        Raises:
            DomainError: ``invalid_configuration`` with ``image`` for an empty, too large,
                unsupported or malformed file.
        """
        media_type = recognise(data)
        if media_type is None or len(data) > limit:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "image")
        try:
            return cls(media_type, _STRIPPERS[media_type](data))
        except (_MalformedError, struct.error) as exc:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "image") from exc

    @property
    def digest(self) -> str:
        """First 16 hex digits of the SHA-256 of the stored content."""
        return content_digest(self.data)

    def ref(self, text: ImageText) -> PageImage:
        """Return the reference pages use to show this image with ``text``."""
        return PageImage(self.media_type, self.digest, text)
