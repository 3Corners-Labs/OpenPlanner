"""The guest identity rules of ADR 0123: pseudonymous subject, display name, hidden token."""

from datetime import UTC, datetime

import pytest

from app.domain.guest_access import (
    CHECK_SUBJECT,
    GUEST_TERM_KEY,
    MAX_DISPLAY_NAME,
    NEUTRAL_TERM,
    SUBJECT_PREFIX,
    DisplayNameMode,
    GuestAccess,
    guest_display_name,
    guest_subject,
)
from app.i18n import Translator, load_catalogues

KEY = b"k" * 32


def test_a_booking_keeps_its_subject_and_every_booking_has_its_own() -> None:
    assert guest_subject(7, KEY) == guest_subject(7, KEY)
    assert guest_subject(7, KEY) != guest_subject(8, KEY)
    assert guest_subject(7, KEY) != guest_subject(7, b"o" * 32)


def test_a_subject_is_marked_as_ours_and_reveals_no_booking_id() -> None:
    subject = guest_subject(1234567, KEY)

    assert subject.startswith(SUBJECT_PREFIX)
    assert "1234567" not in subject
    assert subject != CHECK_SUBJECT
    assert not CHECK_SUBJECT.startswith(SUBJECT_PREFIX)


def test_the_first_name_is_used_trimmed() -> None:
    assert guest_display_name("  Ada ", DisplayNameMode.FIRST_NAME, "Deelnemer") == "Ada"


@pytest.mark.parametrize("first_name", [None, "", "   ", "\x00\n"])
def test_without_a_first_name_the_term_is_used(first_name: str | None) -> None:
    assert guest_display_name(first_name, DisplayNameMode.FIRST_NAME, "Deelnemer") == "Deelnemer"


def test_term_mode_never_sends_the_first_name() -> None:
    assert guest_display_name("Ada", DisplayNameMode.TERM, "Deelnemer") == "Deelnemer"


def test_control_characters_go_and_the_length_is_capped() -> None:
    name = guest_display_name("A\x07d\na‮", DisplayNameMode.FIRST_NAME, "x")
    assert name == "Ada"
    long = guest_display_name("a" * 100, DisplayNameMode.FIRST_NAME, "x")
    assert len(long) == MAX_DISPLAY_NAME


def test_the_token_never_appears_in_repr() -> None:
    access = GuestAccess("secret-token-value", datetime(2026, 1, 1, tzinfo=UTC))

    assert "secret-token-value" not in repr(access)
    assert "secret-token-value" not in str(access)


@pytest.mark.parametrize("mode", list(DisplayNameMode))
def test_the_name_is_never_empty(mode: DisplayNameMode) -> None:
    assert guest_display_name("", mode, "  \x00 ") == NEUTRAL_TERM
    assert guest_display_name(None, mode, "") == NEUTRAL_TERM


def test_the_registry_choices_come_from_the_enums() -> None:
    from app.domain.guest_access import StaffNameMode
    from app.domain.settings_registry import BY_KEY

    assert BY_KEY["embedded_call_display_name"].choices == ("first_name", "term")
    assert BY_KEY["embedded_call_staff_name"].choices == tuple(m.value for m in StaffNameMode)
    assert [m.value for m in StaffNameMode] == ["name", "term"]


def test_the_neutral_word_and_the_catalogues_default_for_a_participant_are_the_same_word() -> None:
    """The rename at the purge sends the constant; the guest is minted under the catalogue word."""
    assert Translator(load_catalogues()).gettext("en", GUEST_TERM_KEY) == NEUTRAL_TERM
