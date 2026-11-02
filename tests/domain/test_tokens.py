import hashlib
import hmac

import pytest

from app.domain.tokens import (
    agent_pseudonym,
    agent_pseudonyms,
    derive_token,
    hash_token,
    new_token_salt,
    token_for_booking,
    token_matches,
)

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


def test_agent_pseudonym_is_stable_per_subject_and_key() -> None:
    first = agent_pseudonym(b"k" * 32, "user-1")

    assert first == agent_pseudonym(b"k" * 32, "user-1")
    assert first != agent_pseudonym(b"k" * 32, "user-2")
    assert first != agent_pseudonym(b"j" * 32, "user-1")
    assert "user-1" not in first
    assert len(first) == 64


def test_the_link_of_a_booking_made_under_the_previous_key_is_kept() -> None:
    old, new = b"o" * 32, b"n" * 32
    salt = new_token_salt()
    stored = hash_token(derive_token(old, salt))

    assert token_for_booking([new, old], salt, stored) == derive_token(old, salt)
    assert token_for_booking([new, old], salt, hash_token(derive_token(new, salt))) == (
        derive_token(new, salt)
    )


def test_without_the_previous_key_a_booking_under_it_gets_a_dead_link() -> None:
    old, new = b"o" * 32, b"n" * 32
    salt = new_token_salt()

    token = token_for_booking([new], salt, hash_token(derive_token(old, salt)))

    assert token == derive_token(new, salt)
    assert not token_matches(token, hash_token(derive_token(old, salt)))


def test_agent_pseudonyms_cover_both_keys() -> None:
    old, new = b"o" * 32, b"n" * 32

    assert agent_pseudonyms([new, old], "u") == (
        agent_pseudonym(new, "u"),
        agent_pseudonym(old, "u"),
    )
