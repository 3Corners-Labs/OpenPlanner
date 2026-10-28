"""Design tokens an organisation may change, their validation and the theme file (ADR 0041).

Citizen pages are styled by ``--booking-*`` custom properties (``site.css``). An organisation
overrides a fixed allow-list of them; every value is checked against its token's type and
bounds here, so the generated ``theme.css`` can only ever contain values this module accepted.
Colours are checked for contrast across tokens when branding is saved (``contrast_failure``).

A theme file is the same set written as CSS: comments, at most one ``:root { ... }`` wrapper
and ``--booking-*`` declarations. Anything else (selectors, at-rules, ``url()``,
``!important``) is refused with the line it is on.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode

PREFIX = "--booking-"
ACCENT_PROPERTY = "color-accent"
MAX_THEME_FILE_BYTES = 8_000
MAX_FONT_STACK_LENGTH = 200
REM_PX = 16

SYSTEM_FONT_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


class TokenKind(StrEnum):
    """How a token's value is written and checked."""

    COLOUR = "colour"
    LENGTH = "length"
    NUMBER = "number"
    WEIGHT = "weight"
    SHADOW = "shadow"
    FONT_STACK = "font_stack"


@dataclass(frozen=True)
class TokenSpec:
    """One editable token.

    Attributes:
        name: The token without ``--booking-``, for example ``color-text``.
        kind: Its type.
        default: The value ``site.css`` uses when the organisation sets nothing.
        low: Lower bound; pixels for lengths.
        high: Upper bound; pixels for lengths.
    """

    name: str
    kind: TokenKind
    default: str
    low: float = 0
    high: float = 0

    @property
    def field(self) -> str:
        """Return the form field and error field of this token (``color_text``)."""
        return self.name.replace("-", "_")


TOKENS: tuple[TokenSpec, ...] = (
    TokenSpec("color-text", TokenKind.COLOUR, "#1A1A1A"),
    TokenSpec("color-text-muted", TokenKind.COLOUR, "#545454"),
    TokenSpec("color-page", TokenKind.COLOUR, "#F5F6F7"),
    TokenSpec("color-surface", TokenKind.COLOUR, "#FFFFFF"),
    TokenSpec("color-background", TokenKind.COLOUR, "#FFFFFF"),
    TokenSpec("color-border", TokenKind.COLOUR, "#D0D4D9"),
    TokenSpec("color-border-strong", TokenKind.COLOUR, "#6B7178"),
    TokenSpec("color-on-accent", TokenKind.COLOUR, "#FFFFFF"),
    TokenSpec("font-family", TokenKind.FONT_STACK, SYSTEM_FONT_STACK),
    TokenSpec("font-family-heading", TokenKind.FONT_STACK, SYSTEM_FONT_STACK),
    TokenSpec("font-size", TokenKind.LENGTH, "17px", 16, 20),
    TokenSpec("line-height", TokenKind.NUMBER, "1.6", 1.4, 2.0),
    TokenSpec("font-weight-heading", TokenKind.WEIGHT, "700", 400, 900),
    TokenSpec("radius", TokenKind.LENGTH, "10px", 0, 24),
    TokenSpec("radius-small", TokenKind.LENGTH, "6px", 0, 16),
    TokenSpec("max-width", TokenKind.LENGTH, "960px", 640, 1440),
    TokenSpec("shadow", TokenKind.SHADOW, "default"),
)
"""Every token an organisation may set; spacing, target size and focus colours are fixed."""

SPECS = {spec.name: spec for spec in TOKENS}

FIXED_COLOURS = {
    "color-error": "#C22A1E",
    "color-success": "#2F7210",
    "color-focus": "#1A1A1A",
    "color-focus-background": "#FFBF47",
}
"""Colours from ``site.css`` that are not editable but take part in the contrast rules."""

_BACKGROUNDS = ("color-page", "color-surface", "color-background")
_TEXT_COLOURS = ("color-text", "color-text-muted")
CONTRAST_RULES: tuple[tuple[str, str, float], ...] = (
    *(
        (fg, bg, 4.5)
        for fg in (
            "color-text",
            "color-text-muted",
            ACCENT_PROPERTY,
            "color-error",
            "color-success",
        )
        for bg in _BACKGROUNDS
    ),
    ("color-on-accent", ACCENT_PROPERTY, 4.5),
    ("color-text", "color-focus-background", 4.5),
    ("color-border-strong", "color-background", 3.0),
    ("color-border-strong", "color-surface", 3.0),
    *(("color-focus", bg, 3.0) for bg in _BACKGROUNDS),
)
"""Foreground, background and minimum ratio: text 4.5:1 (WCAG 1.4.3), parts 3:1 (1.4.11)."""

_HEX_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
_LENGTH = re.compile(r"(\d{1,4}(?:\.\d{1,4})?)(px|rem)")
_NUMBER = re.compile(r"\d(?:\.\d{1,2})?")
_FONT_NAME = re.compile(r'"[A-Za-z0-9][A-Za-z0-9 -]*"|-?[A-Za-z][A-Za-z0-9-]*(?: [A-Za-z0-9-]+)*')
_DECLARATION = re.compile(r"(--booking-[a-z-]+)\s*:\s*(.*)", re.DOTALL)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_ROOT_OPEN = re.compile(r"\s*:root\s*\{")


def _invalid(field: str) -> DomainError:
    """Return the error for an invalid ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def _channel(value: int) -> float:
    """Return the linearised sRGB value of one 8-bit colour channel."""
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """Return the WCAG relative luminance of ``#RRGGBB`` (0 to 1)."""
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    """Return the WCAG contrast ratio of two ``#RRGGBB`` colours (1 to 21)."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def normalise_colour(value: str, field: str) -> str:
    """Return ``#RRGGBB`` upper-case.

    Raises:
        DomainError: ``invalid_configuration`` with ``field`` for anything else.
    """
    if not _HEX_COLOUR.fullmatch(value):
        raise _invalid(field)
    return value.upper()


def _number_text(value: float) -> str:
    """Return ``value`` without trailing zeros (``17``, ``1.0625``)."""
    return f"{value:g}"


def _length(spec: TokenSpec, value: str) -> str:
    """Return a length in ``px`` or ``rem`` within the token's pixel bounds."""
    found = _LENGTH.fullmatch(value)
    if found is None:
        raise _invalid(spec.field)
    amount, unit = float(found[1]), found[2]
    pixels = amount * REM_PX if unit == "rem" else amount
    if not spec.low <= pixels <= spec.high:
        raise _invalid(spec.field)
    return f"{_number_text(amount)}{unit}"


def _number(spec: TokenSpec, value: str) -> str:
    """Return a plain number within the token's bounds."""
    if not _NUMBER.fullmatch(value) or not spec.low <= float(value) <= spec.high:
        raise _invalid(spec.field)
    return _number_text(float(value))


def _weight(spec: TokenSpec, value: str) -> str:
    """Return a font weight: a multiple of 100 within the token's bounds."""
    if not value.isdigit() or int(value) % 100 or not spec.low <= int(value) <= spec.high:
        raise _invalid(spec.field)
    return str(int(value))


def _font_stack(spec: TokenSpec, value: str) -> str:
    """Return a font stack of family names and keywords; no ``url()``, no punctuation."""
    names = [name.strip() for name in value.split(",")]
    if (
        len(value) > MAX_FONT_STACK_LENGTH
        or len(names) > 8
        or not all(_FONT_NAME.fullmatch(name) for name in names)
    ):
        raise _invalid(spec.field)
    return ", ".join(names)


def _shadow(spec: TokenSpec, value: str) -> str:
    """Return ``none``: the only shadow an organisation may choose (switch it off)."""
    if value != "none":
        raise _invalid(spec.field)
    return value


_NORMALISERS = {
    TokenKind.LENGTH: _length,
    TokenKind.NUMBER: _number,
    TokenKind.WEIGHT: _weight,
    TokenKind.FONT_STACK: _font_stack,
    TokenKind.SHADOW: _shadow,
}


def normalise(name: str, value: str) -> str:
    """Return the checked, canonical value of token ``name``.

    Raises:
        DomainError: ``invalid_configuration`` with the token's field (``color_text``) for an
            unknown token or a value outside its type or bounds.
    """
    spec = SPECS.get(name)
    if spec is None:
        raise _invalid(name.replace("-", "_"))
    value = value.strip()
    if spec.kind is TokenKind.COLOUR:
        return normalise_colour(value, spec.field)
    return _NORMALISERS[spec.kind](spec, value)


@dataclass(frozen=True)
class Theme:
    """The tokens an organisation overrides, in canonical form, sorted by name.

    Build with ``Theme.of``; the constructor trusts its input (the adapter reads values this
    module wrote).

    Attributes:
        values: ``(token, value)`` pairs; tokens left out keep their ``site.css`` default.
    """

    values: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, values: Mapping[str, str]) -> "Theme":
        """Validate ``values`` (token name to value) and return the theme.

        Raises:
            DomainError: ``invalid_configuration`` with the first invalid token's field.
        """
        return cls(tuple(sorted((name, normalise(name, v)) for name, v in values.items())))

    def get(self, name: str) -> str | None:
        """Return the override of ``name``, if the organisation set one."""
        return dict(self.values).get(name)

    def resolved(self, name: str) -> str:
        """Return the value of ``name`` as pages use it: the override or the default."""
        override = self.get(name)
        if override is not None:
            return override
        return SPECS[name].default if name in SPECS else FIXED_COLOURS[name]

    def contrast_failure(self, accent: str, accent_default: str) -> str | None:
        """Return the field of the first colour that breaks a contrast rule, or ``None``.

        The field blamed is the one the organisation changed: the foreground if it was
        overridden, else the background, else the foreground.
        """

        def colour(name: str) -> str:
            return accent if name == ACCENT_PROPERTY else self.resolved(name)

        def changed(name: str) -> bool:
            return (
                accent != accent_default if name == ACCENT_PROPERTY else self.get(name) is not None
            )

        for foreground, background, minimum in CONTRAST_RULES:
            if contrast(colour(foreground), colour(background)) < minimum:
                blamed = (
                    background if changed(background) and not changed(foreground) else foreground
                )
                return "accent_color" if blamed == ACCENT_PROPERTY else SPECS[blamed].field
        return None


class ThemeFileError(DomainError):
    """A theme file was refused; ``line`` is where (1-based, 0 for the whole file).

    Args:
        line: The offending line.
    """

    def __init__(self, line: int) -> None:
        """Refuse field ``theme_file`` at ``line``."""
        super().__init__(ErrorCode.INVALID_CONFIGURATION, "theme_file")
        self.line = line


@dataclass(frozen=True)
class ThemeFile:
    """What an uploaded theme file sets: the accent (``None`` when left out) and the theme."""

    accent: str | None
    theme: Theme


def _blank_comments(text: str) -> str:
    """Replace comments by spaces, keeping newlines so line numbers stay right."""
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), text)


def _line(text: str, position: int) -> int:
    """Return the 1-based line of ``position`` in ``text``."""
    return text.count("\n", 0, position) + 1


def _body(text: str) -> tuple[int, int]:
    """Return start and end of the declarations: inside ``:root { }`` or the whole text.

    Values never contain ``}``, so the first one closes the wrapper; nothing may follow it.
    """
    opened = _ROOT_OPEN.match(text)
    if opened is None:
        return 0, len(text)
    end = text.find("}", opened.end())
    if end < 0:
        raise ThemeFileError(_line(text, len(text.rstrip())))
    trailing = text[end + 1 :]
    if trailing.strip():
        raise ThemeFileError(_line(text, end + 1 + len(trailing) - len(trailing.lstrip())))
    return opened.end(), end


def parse_theme_file(data: bytes) -> ThemeFile:
    """Read an uploaded theme file; see the module docstring for what it may contain.

    Raises:
        ThemeFileError: The first offending line; line 0 for an empty, oversized or
            non-UTF-8 file.
    """
    if not data.strip() or len(data) > MAX_THEME_FILE_BYTES:
        raise ThemeFileError(0)
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ThemeFileError(0) from exc
    if "/*" in (text := _blank_comments(raw)):
        raise ThemeFileError(_line(text, text.index("/*")))
    start, end = _body(text)
    values: dict[str, str] = {}
    position = start
    for chunk in text[start:end].split(";"):
        stripped = chunk.strip()
        line = _line(text, position + len(chunk) - len(chunk.lstrip()))
        position += len(chunk) + 1
        if not stripped:
            continue
        found = _DECLARATION.fullmatch(stripped)
        if found is None:
            raise ThemeFileError(line)
        name = found[1].removeprefix(PREFIX)
        try:
            values[name] = (
                normalise_colour(found[2].strip(), "accent_color")
                if name == ACCENT_PROPERTY
                else normalise(name, found[2])
            )
        except DomainError as exc:
            raise ThemeFileError(line) from exc
    accent = values.pop(ACCENT_PROPERTY, None)
    return ThemeFile(accent, Theme(tuple(sorted(values.items()))))


def theme_file_text(accent: str, theme: Theme) -> str:
    """Return ``theme`` as a theme file: set tokens active, the others as commented defaults.

    Uploading the result unchanged gives the same theme back.
    """
    lines = [
        "/* OpenPlanner theme (ADR 0041). Only --booking-* tokens; remove the comment",
        "   markers around a line to change it. Tokens left out use their default. */",
        ":root {",
        f"  {PREFIX}{ACCENT_PROPERTY}: {accent};",
    ]
    for spec in TOKENS:
        value = theme.get(spec.name)
        if value is not None:
            lines.append(f"  {PREFIX}{spec.name}: {value};")
        else:
            default = "none" if spec.kind is TokenKind.SHADOW else spec.default
            lines.append(f"  /* {PREFIX}{spec.name}: {default}; */")
    lines.append("}")
    return "\n".join(lines) + "\n"
