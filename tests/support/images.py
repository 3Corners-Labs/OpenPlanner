"""Small but well-formed PNG, JPEG and WebP files, with and without metadata, for image tests.

The files are not meant to be decoded; they have the container structure the metadata
stripper walks (ADR 0037 §3), with the kinds of metadata a camera or editor writes: EXIF with
a GPS position, XMP with a photographer's name, PNG text chunks and a JPEG comment.
"""

import struct
import zlib

SECRET = b"Ada Lovelace 52.3676N 4.9041E"
"""Stands for the personal data metadata can carry; must never survive an upload."""

EXIF = b"Exif\x00\x00MM\x00*GPS " + SECRET
XMP = b"<x:xmpmeta><dc:creator>" + SECRET + b"</dc:creator></x:xmpmeta>"


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk with its length and CRC."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(*, metadata: bool = True) -> bytes:
    """Return a 1×1 PNG; with ``metadata`` it carries tEXt, iTXt, eXIf and tIME chunks."""
    header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    pixels = png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    extra = (
        png_chunk(b"tEXt", b"Author\x00" + SECRET)
        + png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + XMP)
        + png_chunk(b"eXIf", EXIF[6:])
        + png_chunk(b"tIME", b"\x07\xea\x0a\x1c\x0c\x00\x00")
        if metadata
        else b""
    )
    gamma = png_chunk(b"gAMA", struct.pack(">I", 45455))
    return b"\x89PNG\r\n\x1a\n" + header + gamma + extra + pixels + png_chunk(b"IEND", b"")


def jpeg_segment(marker: int, data: bytes) -> bytes:
    """Return one JPEG marker segment with its length."""
    return bytes((0xFF, marker)) + struct.pack(">H", len(data) + 2) + data


def jpeg(*, metadata: bool = True) -> bytes:
    """Return a JPEG; with ``metadata`` it has EXIF and XMP, IPTC, a comment and a thumbnail.

    The scan data contains a stuffed ``FF 00`` and a restart marker, and an XMP segment
    follows the first scan, as in progressive files.
    """
    jfif = jpeg_segment(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")
    icc = jpeg_segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile")
    tables = jpeg_segment(0xDB, b"\x00" + bytes(64))
    frame = jpeg_segment(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
    scan = jpeg_segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\xff\x00\x34\xff\xd0\x56"
    later = jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + XMP) if metadata else b""
    second = jpeg_segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x78\x9a"
    head = (
        jpeg_segment(0xE1, EXIF)
        + jpeg_segment(0xED, b"Photoshop 3.0\x00" + SECRET)
        + jpeg_segment(0xFE, SECRET)
        + jpeg_segment(0xE0, b"JFXX\x00\x10" + SECRET)  # thumbnail of the original
        + jpeg_segment(0xE2, b"FPXR\x00" + SECRET)  # FlashPix metadata, not a colour profile
        if metadata
        else b""
    )
    return b"\xff\xd8" + jfif + head + icc + tables + frame + scan + later + second + b"\xff\xd9"


def webp_chunk(kind: bytes, data: bytes) -> bytes:
    """Return one RIFF chunk, padded to an even length."""
    return kind + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")


def webp(*, metadata: bool = True) -> bytes:
    """Return an extended WebP; with ``metadata`` it has EXIF and XMP chunks and their flags."""
    flags = 0x08 | 0x04 if metadata else 0
    vp8x = webp_chunk(b"VP8X", bytes((flags, 0, 0, 0)) + b"\x00\x00\x00\x00\x00\x00")
    image = webp_chunk(b"VP8L", b"\x2f\x00\x00\x00\x00\x07")
    extra = webp_chunk(b"EXIF", EXIF[6:]) + webp_chunk(b"XMP ", XMP + b"!") if metadata else b""
    body = b"WEBP" + vp8x + image + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body
