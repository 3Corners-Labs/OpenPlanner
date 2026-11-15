"""The address of an organisation's privacy statement (ADR 0123 point 5, step 6b)."""

import pytest

from app.domain.platform_settings import CHECKED_KEYS, usable
from app.domain.privacy_statement import MAX_LENGTH, privacy_link, valid_privacy_url
from app.domain.settings_registry import BY_KEY, SettingClass

LONG = "https://voorbeeld.nl/" + "a" * (MAX_LENGTH - len("https://voorbeeld.nl/"))


@pytest.mark.parametrize(
    "value",
    [
        "https://voorbeeld.nl/privacy",
        "https://www.voorbeeld.nl:8443/privacy?taal=nl#gegevens",
        "https://[2001:db8::1]/privacy",
        LONG,
        "",  # the setting is optional: no link
    ],
)
def test_an_https_address_is_valid(value: str) -> None:
    assert valid_privacy_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://voorbeeld.nl/privacy",  # not https
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "//voorbeeld.nl/privacy",
        "https:///privacy",  # no host
        "https://",
        "voorbeeld.nl/privacy",
        "https://gebruiker:wachtwoord@voorbeeld.nl/",  # credentials
        "https://voorbeeld.nl/een spatie",
        "https://voorbeeld.nl/\nregel",
        "https://voorbeeld.nl/\u0000",
        "https://voorbeeld.nl\\@evil.example/",
        " https://voorbeeld.nl/",
        "https://voorbeeld.nl:abc/",
        "https://[::1/privacy",  # an unparsable authority
        LONG + "a",  # one over the limit
    ],
)
def test_anything_else_is_refused(value: str) -> None:
    assert not valid_privacy_url(value)
    assert privacy_link(value) is None  # and never becomes a link where the page renders it


def test_the_page_links_only_to_what_is_valid() -> None:
    assert privacy_link("https://voorbeeld.nl/privacy") == "https://voorbeeld.nl/privacy"
    assert privacy_link(None) is None
    assert privacy_link("") is None


def test_the_setting_is_a_class_d_https_address_that_may_be_empty() -> None:
    spec = BY_KEY["privacy_statement_url"]

    assert spec.class_ is SettingClass.ORGANISATION
    assert spec.optional
    assert spec.https_only
    assert spec.default is None
    assert CHECKED_KEYS["privacy_statement_url"] is valid_privacy_url
    assert usable(spec, "https://voorbeeld.nl/privacy")
    assert usable(spec, None)
    assert not usable(spec, "http://voorbeeld.nl/privacy")
    assert not usable(spec, LONG + "a")  # the portal bound of 2,048 characters
