import hashlib

from app.domain.tokens import hash_token, new_management_token, token_matches


def test_new_token_is_url_safe_and_long_enough() -> None:
    token, _ = new_management_token()

    assert len(token) >= 43  # 32 bytes, base64url
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_only_the_sha256_is_kept() -> None:
    token, token_hash = new_management_token()

    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in token_hash


def test_tokens_are_unique() -> None:
    assert len({new_management_token()[0] for _ in range(100)}) == 100


def test_token_matches_in_constant_time() -> None:
    token, token_hash = new_management_token()

    assert token_matches(token, token_hash)
    assert not token_matches(token + "x", token_hash)
    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()
