"""Branding of an organisation: display name, accent colour and logo (WP3b, open point O5).

Citizen pages show the organisation's logo and name and use its accent colour for links,
buttons and the selected day. The accent is used for white text on the accent and for accent
text on white, so one rule covers both: at least 4.5:1 contrast with white (WCAG 1.4.3).
The colour reaches the browser only through a generated stylesheet of CSS custom properties;
validation here is what makes that stylesheet safe to generate.

Logos are PNG, JPEG or WebP, recognised from their bytes, never from a file name or a
client-supplied type. SVG is refused: it can carry scripts.
"""

import hashlib
import re
from dataclasses import dataclass

from app.domain.errors import DomainError, ErrorCode

DEFAULT_ACCENT = "#01689B"
"""NL Design System link blue; 5.9:1 against white."""

MIN_CONTRAST = 4.5
MAX_DISPLAY_NAME_LENGTH = 200
MAX_LOGO_BYTES = 200_000

_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_SIGNATURES: tuple[tuple[tuple[tuple[int, bytes], ...], str], ...] = (
    (((0, b"\x89PNG\r\n\x1a\n"),), "image/png"),
    (((0, b"\xff\xd8\xff"),), "image/jpeg"),
    (((0, b"RIFF"), (8, b"WEBP")), "image/webp"),
)
"""Magic bytes at their offsets, and the media type they identify."""


def _invalid(field: str) -> DomainError:
    """Return the error for an invalid branding ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def _channel(value: int) -> float:
    """Return the linearised sRGB value of one 8-bit colour channel."""
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast_with_white(colour: str) -> float:
    """Return the WCAG contrast ratio of ``#RRGGBB`` against white (1 to 21)."""
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)
    return 1.05 / (luminance + 0.05)


@dataclass(frozen=True)
class LogoRef:
    """What pages need to show a stored logo: its type and a content digest for caching."""

    media_type: str
    digest: str


@dataclass(frozen=True)
class Branding:
    """How citizen pages of an organisation look.

    Attributes:
        display_name: Name in the page header; the organisation's name when ``None``.
            Surrounding white space is removed; an empty name becomes ``None``.
        accent_color: ``#RRGGBB``, stored upper-case, at least 4.5:1 against white.
        logo: The stored logo, if any.

    Raises:
        DomainError: ``invalid_configuration`` with ``display_name`` or ``accent_color``.
    """

    display_name: str | None = None
    accent_color: str = DEFAULT_ACCENT
    logo: LogoRef | None = None

    def __post_init__(self) -> None:
        """Normalise and validate; see the class docstring."""
        name = (self.display_name or "").strip() or None
        if name is not None and (
            len(name) > MAX_DISPLAY_NAME_LENGTH or _CONTROL_CHARS.search(name)
        ):
            raise _invalid("display_name")
        if not _HEX_COLOUR.fullmatch(self.accent_color):
            raise _invalid("accent_color")
        accent = self.accent_color.upper()
        if contrast_with_white(accent) < MIN_CONTRAST:
            raise _invalid("accent_color")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "accent_color", accent)


@dataclass(frozen=True)
class Logo:
    """A validated logo image. Build with ``from_upload``."""

    media_type: str
    data: bytes

    @classmethod
    def from_upload(cls, data: bytes) -> "Logo":
        """Validate uploaded bytes and recognise their type.

        Raises:
            DomainError: ``invalid_configuration`` with ``logo`` for an empty, too large or
                unsupported file.
        """
        if not data or len(data) > MAX_LOGO_BYTES:
            raise _invalid("logo")
        for parts, media_type in _SIGNATURES:
            if all(data[at : at + len(magic)] == magic for at, magic in parts):
                return cls(media_type, data)
        raise _invalid("logo")

    @property
    def digest(self) -> str:
        """First 16 hex digits of the SHA-256 of the content; changes with every new logo."""
        return hashlib.sha256(self.data).hexdigest()[:16]

    @property
    def ref(self) -> LogoRef:
        """The reference pages use to show this logo."""
        return LogoRef(self.media_type, self.digest)
