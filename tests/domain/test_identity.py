"""Provider-qualified subjects (ADR 0083 point 16): abuse cases B6 and B7."""

import pytest

from app.domain.identity import (
    MAX_SUBJECT_LENGTH,
    PLATFORM,
    SubjectRefusedError,
    provider_of,
    qualify,
)


def test_a_platform_subject_stays_bare() -> None:
    assert qualify(PLATFORM, "user-1") == "user-1"
    assert provider_of("user-1") == PLATFORM


def test_b6_the_same_sub_at_two_providers_is_two_identities() -> None:
    first = qualify("gem-a", "1234")
    second = qualify("gem-b", "1234")

    assert first == "idp:gem-a:1234"
    assert first != second != qualify(PLATFORM, "1234")
    assert provider_of(first) == "gem-a"
    assert provider_of(second) == "gem-b"


def test_b7_a_platform_sub_shaped_as_a_qualified_one_is_refused() -> None:
    with pytest.raises(SubjectRefusedError) as refused:
        qualify(PLATFORM, "idp:gem-a:1234")

    assert refused.value.reason == "reserved_subject"


def test_a_sub_with_colons_keeps_its_provider() -> None:
    subject = qualify("gem-a", "urn:x:1")

    assert subject == "idp:gem-a:urn:x:1"
    assert provider_of(subject) == "gem-a"


def test_a_qualified_subject_that_does_not_fit_is_refused() -> None:
    fits = "x" * (MAX_SUBJECT_LENGTH - len("idp:gem-a:"))
    assert len(qualify("gem-a", fits)) == MAX_SUBJECT_LENGTH

    with pytest.raises(SubjectRefusedError) as refused:
        qualify("gem-a", fits + "x")

    assert refused.value.reason == "subject_too_long"


@pytest.mark.parametrize("sub", ["", " "])
def test_an_empty_sub_is_refused(sub: str) -> None:
    with pytest.raises(SubjectRefusedError):
        qualify("gem-a", sub)
    with pytest.raises(SubjectRefusedError):
        qualify(PLATFORM, sub)


@pytest.mark.parametrize("key", ["Gem-A", "-gem", "gem a", "gem:a", ""])
def test_an_organisation_provider_key_follows_the_idp_value_grammar(key: str) -> None:
    if key == PLATFORM:
        assert qualify(key, "1") == "1"
        return
    with pytest.raises(ValueError, match="provider key"):
        qualify(key, "1")
