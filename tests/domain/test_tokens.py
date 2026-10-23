import hashlib
import hmac

import pytest

from app.domain.tokens import derive_token, hash_token, new_token_salt, token_matches

KEY = b"k" * 32


def test_salt_is_32_random_url_safe_bytes() -> None:
    salt = new_token_salt()

    assert len(salt) >= 43
    assert set(salt) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert len({new_token_salt() for _ in range(100)}) == 100


def test_token_is_derived_from_key_and_salt() -> None:
    salt = new_token_salt()

    token = derive_token(KEY, salt)

    assert token == derive_token(KEY, salt)
    assert token != derive_token(b"o" * 32, salt)
    assert token != derive_token(KEY, new_token_salt())
    assert len(token) == 43  # 32 bytes, base64url without padding
    expected = hmac.new(KEY, b"manage:" + salt.encode(), hashlib.sha256).digest()
    assert token.encode() != expected


def test_only_the_sha256_of_the_token_is_kept() -> None:
    token = derive_token(KEY, new_token_salt())

    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_token_matches_in_constant_time() -> None:
    token = derive_token(KEY, new_token_salt())

    assert token_matches(token, hash_token(token))
    assert not token_matches(token + "x", hash_token(token))


@pytest.mark.parametrize("key", [b"", b"x" * 31])
def test_short_keys_are_rejected(key: bytes) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        derive_token(key, new_token_salt())
