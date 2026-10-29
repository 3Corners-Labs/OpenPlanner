"""Uploaded images (ADR 0037 §3): type from the bytes, metadata stripped, a text alternative."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.images import (
    MAX_ALT_LENGTH,
    MAX_IMAGE_BYTES,
    ImageFile,
    ImageText,
    recognise,
)
from tests.support.images import SECRET, jpeg, jpeg_segment, png, png_chunk, webp, webp_chunk


def _field(error: pytest.ExceptionInfo[DomainError]) -> str | None:
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    return error.value.field


@pytest.mark.parametrize(
    ("data", "media_type"),
    [(png(), "image/png"), (jpeg(), "image/jpeg"), (webp(), "image/webp")],
)
def test_the_type_comes_from_the_magic_bytes(data: bytes, media_type: str) -> None:
    assert recognise(data) == media_type
    assert ImageFile.from_upload(data).media_type == media_type


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<html><script>alert(1)</script></html>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"GIF89a" + bytes(16),
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
    ],
)
def test_html_svg_gif_and_other_files_are_refused(data: bytes) -> None:
    assert recognise(data) is None
    with pytest.raises(DomainError) as error:
        ImageFile.from_upload(data)
    assert _field(error) == "image"


def test_an_image_above_the_limit_is_refused() -> None:
    big = (
        png(metadata=False)[:-12]
        + png_chunk(b"IDAT", bytes(MAX_IMAGE_BYTES))
        + png_chunk(b"IEND", b"")
    )
    with pytest.raises(DomainError) as error:
        ImageFile.from_upload(big)
    assert _field(error) == "image"


@pytest.mark.parametrize("make", [png, jpeg, webp])
def test_metadata_is_stripped(make: object) -> None:
    assert callable(make)
    original: bytes = make()
    assert SECRET in original

    stored = ImageFile.from_upload(original).data

    assert SECRET not in stored
    assert b"Exif" not in stored
    assert stored == make(metadata=False)  # everything else is kept, byte for byte


def test_jpeg_fill_bytes_and_standalone_markers_are_kept() -> None:
    clean = jpeg(metadata=False)
    # A fill byte before a marker and a TEM marker between segments, both allowed by JPEG.
    odd = clean[:2] + b"\xff\xff\x01" + clean[2:]
    assert ImageFile.from_upload(odd).data == clean[:2] + b"\xff\x01" + clean[2:]


def test_webp_without_metadata_keeps_its_flags_and_size() -> None:
    clean = webp(metadata=False)
    assert ImageFile.from_upload(clean).data == clean


@pytest.mark.parametrize(
    "data",
    [
        png()[:40],  # cut off inside a chunk
        png(metadata=False)[:-12],  # no IEND
        b"\x89PNG\r\n\x1a\n" + png_chunk(b"IDAT", b"x"),  # no IHDR first
        b"\xff\xd8\xff\xe0\x00",  # segment length cut off
        b"\xff\xd8" + jpeg_segment(0xE0, b"JFIF\x00") + b"\x00\x00",  # no marker
        b"\xff\xd8" + jpeg_segment(0xE0, b"JFIF\x00"),  # no scan, no end
        b"\xff\xd8\xff\xe0\x00\x01JFIF",  # segment length below its own two bytes
        b"\xff\xd8" + jpeg_segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34",  # scan, no end
        webp(metadata=False)[:4] + b"\x27\x00\x00\x00" + webp(metadata=False)[8:] + b"VP8",
        b"RIFF\x40\x00\x00\x00WEBP" + webp_chunk(b"VP8L", b"\x2f"),  # size does not match
        b"RIFF\x0c\x00\x00\x00WEBPVP8L\xff\x00\x00\x00",  # chunk runs past the end
        b"RIFF\x04\x00\x00\x00WEBP",  # no image chunk
    ],
)
def test_malformed_files_are_refused(data: bytes) -> None:
    with pytest.raises(DomainError) as error:
        ImageFile.from_upload(data)
    assert _field(error) == "image"


def test_the_digest_follows_the_stored_content() -> None:
    assert ImageFile.from_upload(png()).digest == ImageFile.from_upload(png(metadata=False)).digest
    assert ImageFile.from_upload(png()).digest != ImageFile.from_upload(jpeg()).digest
    assert len(ImageFile.from_upload(png()).digest) == 16


def test_alt_text_is_required_in_every_locale() -> None:
    text = ImageText({"nl": "  Gesloten deur ", "en": "Closed door"})
    assert text.alt("nl") == "Gesloten deur"
    assert text.alt("en") == "Closed door"
    assert text.alt("de") == "Gesloten deur"  # falls back to Dutch
    assert not text.decorative

    with pytest.raises(DomainError) as error:
        ImageText({"nl": "Gesloten deur", "en": " "})
    assert _field(error) == "alt_en"
    with pytest.raises(DomainError) as error:
        ImageText({})
    assert _field(error) == "alt_nl"


@pytest.mark.parametrize("text", ["x" * (MAX_ALT_LENGTH + 1), "twee\nregels", "tab\x07"])
def test_alt_text_is_short_and_on_one_line(text: str) -> None:
    with pytest.raises(DomainError) as error:
        ImageText({"nl": text, "en": "ok"})
    assert _field(error) == "alt_nl"


def test_a_decorative_image_has_an_empty_alt_and_drops_texts() -> None:
    text = ImageText({"nl": "vergeten", "en": ""}, decorative=True)
    assert text.decorative
    assert text.alts == {}
    assert text.alt("nl") == ""


def test_a_file_and_its_text_make_the_reference_pages_use() -> None:
    image = ImageFile.from_upload(jpeg())
    text = ImageText({"nl": "Deur", "en": "Door"})

    ref = image.ref(text)

    assert (ref.media_type, ref.digest, ref.text) == ("image/jpeg", image.digest, text)
