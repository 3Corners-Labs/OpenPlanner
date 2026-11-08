"""Decoding and encoding a configuration document as JSON text (ADR 0099 point 10)."""

import json

import pytest

from app.adapters.configuration_json import MAX_DEPTH, JsonCodec, decode, encode
from app.domain.errors import DomainError, ErrorCode


def refused(data: bytes, code: ErrorCode, limit: int = 10_000) -> None:
    with pytest.raises(DomainError) as error:
        decode(data, limit)
    assert error.value.code is code


def test_a_document_is_decoded() -> None:
    assert decode(b'{"format": "x", "list": [1, 2.5, null, true]}', 1000) == {
        "format": "x",
        "list": [1, 2.5, None, True],
    }


def test_more_bytes_than_the_limit_are_refused_before_they_are_read() -> None:
    refused(b'{"a": 1}', ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, limit=4)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{not json",
        b'{"a": 1, "a": 2}',
        b'{"nested": {"a": 1, "a": 2}}',
        b'{"a": NaN}',
        b'{"a": Infinity}',
        b'{"a": -Infinity}',
        b"\xff\xfe",
    ],
)
def test_text_that_is_not_one_unambiguous_json_value_is_refused(data: bytes) -> None:
    refused(data, ErrorCode.CONFIGURATION_PACKAGE_INVALID)


def test_nesting_deeper_than_the_format_is_refused() -> None:
    deep = b"[" * (MAX_DEPTH + 2) + b"]" * (MAX_DEPTH + 2)
    ok = b"[" * 5 + b"]" * 5

    refused(deep, ErrorCode.CONFIGURATION_PACKAGE_INVALID)
    assert decode(ok, 1000) == [[[[[]]]]]


def test_extremely_deep_nesting_does_not_crash() -> None:
    refused(b"[" * 100_000 + b"]" * 100_000, ErrorCode.CONFIGURATION_PACKAGE_INVALID, limit=10**6)


def test_a_document_is_encoded_readably_and_reads_back() -> None:
    document = {"format": "x", "text": "é ü", "n": [1, {"b": None}]}

    text = encode(document)

    assert text.endswith(b"\n")
    assert "é ü" in text.decode()
    assert json.loads(text) == document
    assert JsonCodec().decode(JsonCodec().encode(document), 1000) == document


def test_a_number_json_cannot_say_is_not_encoded() -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the text differs between Python versions
        encode({"x": float("nan")})
