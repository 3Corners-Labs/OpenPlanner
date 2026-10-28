"""Branding per organisation (WP3b, O5): accent colour with contrast check, logo checks."""

import pytest

from app.domain.branding import (
    DEFAULT_ACCENT,
    MAX_FONT_BYTES,
    MAX_LOGO_BYTES,
    Branding,
    FontFile,
    FontRef,
    FontRole,
    HeaderStyle,
    Logo,
    LogoRef,
    contrast_with_white,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.theme import Theme

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
WOFF2 = b"wOF2" + b"\x00" * 64


def _field(error: pytest.ExceptionInfo[DomainError]) -> str | None:
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    return error.value.field


def test_default_branding_passes_its_own_contrast_rule() -> None:
    assert Branding().accent_color == DEFAULT_ACCENT
    assert contrast_with_white(DEFAULT_ACCENT) >= 4.5


@pytest.mark.parametrize(
    ("colour", "expected"),
    [("#000000", 21.0), ("#FFFFFF", 1.0), ("#767676", 4.54)],
)
def test_contrast_follows_wcag_relative_luminance(colour: str, expected: float) -> None:
    assert contrast_with_white(colour) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("colour", ["#154273", "#01689b", "#000000", "#767676"])
def test_dark_enough_accents_are_accepted(colour: str) -> None:
    assert Branding(accent_color=colour).accent_color == colour.upper()


@pytest.mark.parametrize("colour", ["#ffbf47", "#FFFFFF", "#777777", "#e17000"])
def test_accents_without_4_5_to_1_against_white_are_refused(colour: str) -> None:
    with pytest.raises(DomainError) as error:
        Branding(accent_color=colour)
    assert _field(error) == "accent_color"


@pytest.mark.parametrize(
    "colour", ["01689B", "#01689", "#01689BB", "#GGGGGG", "red", "#01689B;}", ""]
)
def test_only_six_digit_hex_colours_are_accepted(colour: str) -> None:
    with pytest.raises(DomainError) as error:
        Branding(accent_color=colour)
    assert _field(error) == "accent_color"


def test_display_name_is_optional_and_trimmed() -> None:
    assert Branding(display_name="  ").display_name is None
    assert Branding(display_name=" Gemeente Voorbeeld ").display_name == "Gemeente Voorbeeld"


@pytest.mark.parametrize("name", ["x" * 201, "Gemeente\nVoorbeeld"])
def test_display_name_rejects_long_or_control_characters(name: str) -> None:
    with pytest.raises(DomainError) as error:
        Branding(display_name=name)
    assert _field(error) == "display_name"


@pytest.mark.parametrize(
    ("data", "media_type"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")],
)
def test_logo_type_comes_from_the_bytes(data: bytes, media_type: str) -> None:
    logo = Logo.from_upload(data)
    assert logo.media_type == media_type
    assert len(logo.digest) == 16


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"GIF89a" + b"\x00" * 32,
        b"<html>",
    ],
)
def test_svg_html_gif_and_empty_files_are_refused(data: bytes) -> None:
    with pytest.raises(DomainError) as error:
        Logo.from_upload(data)
    assert _field(error) == "logo"


def test_a_logo_above_the_size_limit_is_refused() -> None:
    with pytest.raises(DomainError) as error:
        Logo.from_upload(PNG + b"\x00" * MAX_LOGO_BYTES)
    assert _field(error) == "logo"


def test_the_digest_changes_with_the_content() -> None:
    assert Logo.from_upload(PNG).digest != Logo.from_upload(PNG + b"\x01").digest


def test_webp_needs_the_riff_header_too() -> None:
    with pytest.raises(DomainError):
        Logo.from_upload(b"XXXX\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32)


def test_fonts_must_be_woff2_and_small() -> None:
    font = FontFile.from_upload(FontRole.HEADING, WOFF2)
    assert font.ref == FontRef(FontRole.HEADING, font.digest)
    for data in (
        b"",
        b"wOFF" + b"\x00" * 64,
        b"\x00\x01\x00\x00",
        WOFF2 + b"\x00" * MAX_FONT_BYTES,
    ):
        with pytest.raises(DomainError) as error:
            FontFile.from_upload(FontRole.TEXT, data)
        assert _field(error) == "font"


def test_logo_header_without_a_logo_falls_back_to_standard() -> None:
    assert Branding(header_style=HeaderStyle.LOGO).effective_header is HeaderStyle.STANDARD
    with_logo = Branding(header_style=HeaderStyle.LOGO, logo=LogoRef("image/png", "a" * 16))
    assert with_logo.effective_header is HeaderStyle.LOGO


def test_fonts_are_kept_one_per_role_in_order() -> None:
    heading, text = FontRef(FontRole.HEADING, "h" * 16), FontRef(FontRole.TEXT, "t" * 16)
    branding = Branding(fonts=(text, heading))
    assert branding.fonts == (heading, text)
    assert branding.font(FontRole.TEXT) == text
    assert branding.font(FontRole.TEXT_BOLD) is None


def test_the_theme_digest_changes_with_accent_tokens_and_fonts() -> None:
    base = Branding()
    digests = {
        base.theme_digest,
        Branding(accent_color="#154273").theme_digest,
        Branding(theme=Theme.of({"radius": "0px"})).theme_digest,
        Branding(fonts=(FontRef(FontRole.TEXT, "t" * 16),)).theme_digest,
    }
    assert len(digests) == 4
    assert Branding(display_name="Ander").theme_digest == base.theme_digest


def test_contrast_failure_covers_the_accent_and_the_theme() -> None:
    assert Branding().contrast_failure() is None
    dark_page = Branding(theme=Theme.of({"color-page": "#202020"}))
    assert dark_page.contrast_failure() == "color_page"
