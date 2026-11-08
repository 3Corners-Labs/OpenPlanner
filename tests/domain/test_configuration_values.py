"""The values of a configuration document (ADR 0099): secrets, binaries and canonical JSON."""

import base64
import hashlib

import pytest

from app.domain.configuration_values import (
    Binary,
    Sealed,
    Secret,
    canonical_json,
    document_digest,
    redact,
)


def test_a_secret_never_shows_its_plain_text() -> None:
    secret = Secret("hunter2-hunter2")

    assert "hunter2" not in repr(secret)
    assert "hunter2" not in str(secret)
    assert "hunter2" not in repr({"field": secret})
    assert secret.reveal() == "hunter2-hunter2"


def test_secrets_compare_by_digest_so_equal_secrets_are_not_a_change() -> None:
    assert Secret("one") == Secret("one")
    assert Secret("one") != Secret("two")
    assert hash(Secret("one")) == hash(Secret("one"))
    assert Secret("one") != "one"
    assert Secret("one").digest != Secret("two").digest


def test_the_digest_of_a_secret_is_not_a_plain_hash_of_it() -> None:
    # A bare SHA-256 of a weak secret can be guessed offline; the digest is keyed per process.
    assert Secret("one").digest != hashlib.sha256(b"one").hexdigest()


def test_a_sealed_value_and_an_opened_secret_are_never_equal() -> None:
    assert Sealed("YWJj") != Secret("abc")


def test_a_binary_knows_its_digest_and_hides_its_bytes() -> None:
    binary = Binary("image/png", b"\x89PNG-data")

    assert binary.sha256 == hashlib.sha256(b"\x89PNG-data").hexdigest()
    assert "PNG-data" not in repr(binary)
    assert Binary("image/png", b"x") == Binary("image/png", b"x")
    assert Binary("image/png", b"x") != Binary("image/gif", b"x")
    assert Binary("image/png", b"x") != Binary("image/png", b"y")


def test_canonical_json_ignores_key_order_and_spacing() -> None:
    first = canonical_json({"b": [1, 2, {"d": None, "c": True}], "a": "é"})
    second = canonical_json({"a": "é", "b": [1, 2, {"c": True, "d": None}]})

    assert first == second == '{"a":"é","b":[1,2,{"c":true,"d":null}]}'


def test_canonical_json_refuses_what_json_cannot_say() -> None:
    with pytest.raises(ValueError, match="Out of range"):
        canonical_json({"x": float("nan")})


def test_the_document_digest_follows_the_content_only() -> None:
    one = document_digest({"format": "f", "organisations": [{"slug": "a"}]})
    same = document_digest({"organisations": [{"slug": "a"}], "format": "f"})
    other = document_digest({"format": "f", "organisations": [{"slug": "b"}]})

    assert one == same
    assert one != other
    assert len(one) == 64


def test_redact_hides_every_secret_and_binary_in_a_nested_value() -> None:
    value = {
        "token": Secret("tt"),
        "logo": Binary("image/png", b"png"),
        "sealed": Sealed("c2VhbGVk"),
        "list": [Secret("a"), 1],
        "plain": "keep",
    }

    shown = redact(value)

    assert shown == {
        "token": "<secret>",
        "logo": "<binary image/png>",
        "sealed": "<sealed>",
        "list": ["<secret>", 1],
        "plain": "keep",
    }


def test_the_sealed_form_is_base64() -> None:
    token = base64.b64encode(b"sealed-bytes").decode()

    assert Sealed(token).token == token


def test_a_sealed_token_is_not_printed() -> None:
    assert "c2VhbGVk" not in repr(Sealed("c2VhbGVk"))
