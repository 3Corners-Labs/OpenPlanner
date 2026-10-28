"""Branding of an organisation: name, accent, logo, theme, header style and fonts (O5).

Citizen pages show the organisation's logo and name and use its accent colour for links,
buttons and the selected day. The accent is used for white text on the accent and for accent
text on white, so one rule covers both: at least 4.5:1 contrast with white (WCAG 1.4.3).
The colour reaches the browser only through a generated stylesheet of CSS custom properties;
validation here is what makes that stylesheet safe to generate.

Logos are PNG, JPEG or WebP, recognised from their bytes, never from a file name or a
client-supplied type. SVG is refused: it can carry scripts. Fonts are WOFF2 only, recognised
the same way (ADR 0041). The other design tokens live in ``app.domain.theme``.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.theme import Theme, contrast, normalise_colour

DEFAULT_ACCENT = "#01689B"
"""NL Design System link blue; 5.9:1 against white."""

MIN_CONTRAST = 4.5
MAX_DISPLAY_NAME_LENGTH = 200
MAX_LOGO_BYTES = 200_000
MAX_FONT_BYTES = 250_000

_WOFF2 = b"wOF2"
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


def content_digest(data: bytes) -> str:
    """Return the first 16 hex digits of the SHA-256 of ``data``; versions asset URLs."""
    return hashlib.sha256(data).hexdigest()[:16]


def contrast_with_white(colour: str) -> float:
    """Return the WCAG contrast ratio of ``#RRGGBB`` against white (1 to 21)."""
    return contrast(colour, "#FFFFFF")


class HeaderStyle(StrEnum):
    """How the page header shows the organisation (ADR 0041 §6)."""

    STANDARD = "standard"
    """Logo and name on the background colour, with an accent line."""
    ACCENT = "accent"
    """Logo and name on a header filled with the accent colour."""
    LOGO = "logo"
    """The logo only; its alternative text carries the name. Needs a logo."""


class FontRole(StrEnum):
    """Where an uploaded font is used."""

    TEXT = "text"
    TEXT_BOLD = "text-bold"
    HEADING = "heading"


@dataclass(frozen=True)
class FontRef:
    """What the theme needs to declare a stored font: its role and a content digest."""

    role: FontRole
    digest: str


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
        theme: The other tokens the organisation overrides (ADR 0041).
        header_style: How the header shows logo and name.
        fonts: The stored fonts, one per role at most, sorted by role.

    The contrast rules across tokens are checked by ``contrast_failure`` when branding is
    saved, not here, so a stricter rule never breaks reading what is stored.

    Raises:
        DomainError: ``invalid_configuration`` with ``display_name`` or ``accent_color``.
    """

    display_name: str | None = None
    accent_color: str = DEFAULT_ACCENT
    logo: LogoRef | None = None
    theme: Theme = Theme()
    header_style: HeaderStyle = HeaderStyle.STANDARD
    fonts: tuple[FontRef, ...] = ()

    def __post_init__(self) -> None:
        """Normalise and validate; see the class docstring."""
        name = (self.display_name or "").strip() or None
        if name is not None and (
            len(name) > MAX_DISPLAY_NAME_LENGTH or _CONTROL_CHARS.search(name)
        ):
            raise _invalid("display_name")
        accent = normalise_colour(self.accent_color, "accent_color")
        if contrast_with_white(accent) < MIN_CONTRAST:
            raise _invalid("accent_color")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "accent_color", accent)
        object.__setattr__(self, "fonts", tuple(sorted(self.fonts, key=lambda f: f.role)))

    def contrast_failure(self) -> str | None:
        """Return the field of the first colour that breaks a contrast rule, or ``None``."""
        return self.theme.contrast_failure(self.accent_color, DEFAULT_ACCENT)

    def font(self, role: FontRole) -> FontRef | None:
        """Return the stored font of ``role``, if any."""
        return next((f for f in self.fonts if f.role is role), None)

    @property
    def effective_header(self) -> HeaderStyle:
        """Return the header style pages use: ``logo`` without a logo falls back."""
        if self.header_style is HeaderStyle.LOGO and self.logo is None:
            return HeaderStyle.STANDARD
        return self.header_style

    @property
    def theme_digest(self) -> str:
        """First 16 hex digits of a hash over everything ``theme.css`` is made of."""
        parts = [self.accent_color, *(f"{n}={v}" for n, v in self.theme.values)]
        parts += [f"{f.role}={f.digest}" for f in self.fonts]
        return content_digest("\n".join(parts).encode())


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
        return content_digest(self.data)

    @property
    def ref(self) -> LogoRef:
        """The reference pages use to show this logo."""
        return LogoRef(self.media_type, self.digest)


@dataclass(frozen=True)
class FontFile:
    """A validated WOFF2 font for one role. Build with ``from_upload``."""

    role: FontRole
    data: bytes

    @classmethod
    def from_upload(cls, role: FontRole, data: bytes) -> "FontFile":
        """Validate uploaded bytes: WOFF2 by its signature, at most ``MAX_FONT_BYTES``.

        Raises:
            DomainError: ``invalid_configuration`` with ``font`` for an empty, too large or
                non-WOFF2 file.
        """
        if not data.startswith(_WOFF2) or len(data) > MAX_FONT_BYTES:
            raise _invalid("font")
        return cls(role, data)

    @property
    def digest(self) -> str:
        """First 16 hex digits of the SHA-256 of the content."""
        return content_digest(self.data)

    @property
    def ref(self) -> FontRef:
        """The reference the theme uses to declare this font."""
        return FontRef(self.role, self.digest)
