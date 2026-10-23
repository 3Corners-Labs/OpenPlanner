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
