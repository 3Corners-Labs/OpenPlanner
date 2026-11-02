"""Builder of structurally valid WOFF2 files for tests (ADR 0092), without a font library.

The result has a correct header and table directory but its compressed block is filler, so it
is not a usable font; the domain validates structure, never glyphs.
"""

import struct

_HEADER = struct.Struct(">4s4sIHHIIHHIIIII")
HEADER_SIZE = _HEADER.size


def uint_base128(value: int) -> bytes:
    """Encode ``value`` as a WOFF2 ``UIntBase128``."""
    groups = [value & 0x7F]
    value >>= 7
    while value:
        groups.append(value & 0x7F | 0x80)
        value >>= 7
    return bytes(reversed(groups))


def make_woff2(
    fill: int = 0,
    *,
    tables: int = 2,
    sfnt_size: int = 4_000,
    compressed: int = 64,
    length: int | None = None,
    reserved: int = 0,
    directory: bytes | None = None,
) -> bytes:
    """Return WOFF2 bytes; every argument can be bent to build a malformed file.

    Args:
        fill: Byte that fills the compressed block, so different fonts differ.
        tables: ``numTables`` in the header and the number of directory entries built.
        sfnt_size: ``totalSfntSize`` in the header.
        compressed: Size of the compressed block and ``totalCompressedSize``.
        length: ``length`` in the header; the real size when ``None``.
        reserved: The reserved header field.
        directory: Raw table directory instead of the generated one.
    """
    if directory is None:
        # known tags 12..51 (not glyf or loca) at version 0, so not transformed: origLength only.
        directory = b"".join(bytes([12 + i % 40]) + uint_base128(100 + i) for i in range(tables))
    block = bytes([fill]) * compressed
    body = directory + block
    total = HEADER_SIZE + len(body)
    header = _HEADER.pack(
        b"wOF2",
        b"\x00\x01\x00\x00",
        total if length is None else length,
        tables,
        reserved,
        sfnt_size,
        compressed,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return header + body
