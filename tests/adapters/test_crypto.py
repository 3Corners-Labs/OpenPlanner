import pytest
from cryptography.exceptions import InvalidTag

from app.adapters.crypto import SecretBox

KEY = b"k" * 32


def test_sealed_values_open_only_under_their_name() -> None:
    box = SecretBox(KEY)
    nonce, ciphertext = box.seal("webex_service_app", b'{"access_token": "secret"}')

    assert b"secret" not in ciphertext
    assert box.open("webex_service_app", nonce, ciphertext) == b'{"access_token": "secret"}'
    with pytest.raises(InvalidTag):
        box.open("another_record", nonce, ciphertext)
    with pytest.raises(InvalidTag):
        SecretBox(b"x" * 32).open("webex_service_app", nonce, ciphertext)


def test_every_seal_uses_a_fresh_nonce() -> None:
    box = SecretBox(KEY)

    assert box.seal("n", b"v")[0] != box.seal("n", b"v")[0]


def test_a_value_sealed_before_key_ids_still_opens() -> None:
    """Legacy values have a bare 12-byte nonce (ADR 0094 point 2) and open under any key."""
    box = SecretBox(KEY)
    nonce, ciphertext = box.seal("n", b"old")
    legacy_nonce = nonce[:12]

    assert box.open("n", legacy_nonce, ciphertext) == b"old"
    assert not box.is_current(legacy_nonce)  # `rekey` gives it an id
    assert box.is_current(nonce)


def test_a_value_under_the_previous_key_opens_only_while_it_is_configured() -> None:
    old, new = b"o" * 32, b"n" * 32
    nonce, ciphertext = SecretBox(old).seal("n", b"v")

    assert SecretBox(new, [old]).open("n", nonce, ciphertext) == b"v"
    assert not SecretBox(new, [old]).is_current(nonce)
    with pytest.raises(InvalidTag):
        SecretBox(new).open("n", nonce, ciphertext)


def test_a_legacy_value_under_the_previous_key_opens_by_trying_each_key() -> None:
    old, new = b"o" * 32, b"n" * 32
    nonce, ciphertext = SecretBox(old).seal("n", b"v")

    assert SecretBox(new, [old]).open("n", nonce[:12], ciphertext) == b"v"


def test_new_values_are_sealed_under_the_current_key_only() -> None:
    old, new = b"o" * 32, b"n" * 32
    box = SecretBox(new, [old])
    nonce, ciphertext = box.seal("n", b"v")

    assert box.is_current(nonce)
    assert SecretBox(new).open("n", nonce, ciphertext) == b"v"
    assert len(nonce) == 12 + 8
