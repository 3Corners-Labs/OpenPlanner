import pytest

from app.i18n.negotiation import (
    match_locale,
    negotiate_locale,
    normalise_locale,
    parse_accept_language,
)

ENABLED = ("nl", "en")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("", []),
        ("en", ["en"]),
        ("en-GB,en;q=0.9,nl;q=0.8", ["en-gb", "en", "nl"]),
        ("nl;q=0.2, en;q=0.9", ["en", "nl"]),
        ("de;q=0, en", ["en"]),
        ("*, en;q=0.5", ["en"]),
        ("en;q=abc, nl", ["nl"]),
        ("en;q=1.5, nl", ["nl"]),
        ("nl;q=1, en", ["nl", "en"]),
        ("NL-be", ["nl-be"]),
        ("<script>, nl", ["nl"]),
    ],
)
def test_parse_accept_language(header: str, expected: list[str]) -> None:
    assert parse_accept_language(header) == expected


def test_parse_keeps_header_order_for_equal_weights() -> None:
    assert parse_accept_language("en, nl, de") == ["en", "nl", "de"]


def test_parse_ignores_absurdly_long_headers() -> None:
    assert parse_accept_language("en," * 10_000) == ["en"] * 32


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("EN", "en"), ("en_GB", "en-gb"), (" nl ", "nl"), ("x", None), ("../etc", None), (None, None)],
)
def test_normalise_locale(raw: str | None, expected: str | None) -> None:
    assert normalise_locale(raw) == expected


def test_default_is_dutch() -> None:
    assert negotiate_locale(enabled=ENABLED) == "nl"


def test_explicit_choice_wins() -> None:
    result = negotiate_locale(
        enabled=ENABLED, explicit="en", cookie="nl", accept_language="nl", organisation_default="nl"
    )
    assert result == "en"


def test_cookie_beats_browser() -> None:
    assert negotiate_locale(enabled=ENABLED, cookie="en", accept_language="nl") == "en"


def test_browser_language_with_region_falls_back_to_language() -> None:
    assert negotiate_locale(enabled=ENABLED, accept_language="en-GB,de;q=0.5") == "en"


def test_browser_beats_organisation_default() -> None:
    assert (
        negotiate_locale(enabled=ENABLED, accept_language="en", organisation_default="nl") == "en"
    )


def test_organisation_default_when_browser_has_no_match() -> None:
    assert (
        negotiate_locale(enabled=ENABLED, accept_language="de, fr", organisation_default="en")
        == "en"
    )


def test_disabled_or_invalid_choices_are_skipped() -> None:
    assert negotiate_locale(enabled=ENABLED, explicit="de", cookie="<x>") == "nl"


def test_dutch_is_the_default_even_when_not_listed_first() -> None:
    assert negotiate_locale(enabled=("en", "nl")) == "nl"


def test_falls_back_to_first_enabled_when_dutch_is_disabled() -> None:
    assert negotiate_locale(enabled=("en",)) == "en"


def test_requires_at_least_one_enabled_locale() -> None:
    with pytest.raises(ValueError, match="enabled"):
        negotiate_locale(enabled=())


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [("en", "en"), ("EN-us", "en"), ("de", None), ("", None), (None, None)],
)
def test_match_locale(candidate: str | None, expected: str | None) -> None:
    assert match_locale(candidate, ENABLED) == expected
