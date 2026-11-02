"""The proof of a second factor in an ID token (ADR 0100)."""

import pytest

from app.domain.mfa import MFA_AMR_VALUES, shows_mfa


@pytest.mark.parametrize("amr", sorted(MFA_AMR_VALUES))
def test_each_usual_amr_value_shows_mfa(amr: str) -> None:
    assert shows_mfa(["pwd", amr], None, frozenset())


def test_a_password_alone_shows_nothing() -> None:
    assert not shows_mfa(["pwd"], None, frozenset())


def test_a_missing_amr_and_acr_shows_nothing() -> None:
    assert not shows_mfa(None, None, frozenset({"mfa"}))


def test_the_amr_may_be_a_single_string() -> None:
    assert shows_mfa("otp", None, frozenset())
    assert not shows_mfa("pwd otp-ish", None, frozenset())


def test_the_requested_acr_shows_mfa_when_returned() -> None:
    assert shows_mfa(None, "mfa", frozenset({"mfa", "loa2"}))


def test_an_acr_that_was_not_asked_for_shows_nothing() -> None:
    assert not shows_mfa(None, "0", frozenset({"mfa"}))
    assert not shows_mfa(None, "mfa", frozenset())


def test_garbage_types_show_nothing() -> None:
    assert not shows_mfa({"otp": 1}, 5, frozenset({"5"}))
    assert not shows_mfa([1, None], None, frozenset())


def test_the_accepted_values_are_the_callers_to_choose() -> None:
    assert shows_mfa(["pwd", "x-strong"], None, [], ["x-strong"])
    assert not shows_mfa(["otp"], None, [], ["x-strong"])
    assert shows_mfa(None, "urn:strong", ["urn:strong"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("  otp   mfa otp ", "otp mfa"),
        ("urn:oasis:names:tc:SAML:2.0:ac:classes:X", "urn:oasis:names:tc:SAML:2.0:ac:classes:X"),
        ("a<b", None),
        ("x" * 101, None),
        (" ".join(f"v{i}" for i in range(11)), None),
    ],
)
def test_mfa_values_are_plain_tokens_within_bounds(text: str, expected: str | None) -> None:
    from app.domain.mfa import parse_mfa_values

    assert parse_mfa_values(text) == expected
