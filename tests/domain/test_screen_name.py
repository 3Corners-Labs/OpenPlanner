"""What a screen name may be and what the participant is told of the staff member (ADR 0123 5c)."""

import pytest

from app.domain.guest_access import StaffNameMode
from app.domain.screen_name import (
    MAX_LENGTH,
    MIN_LENGTH,
    NO_LABEL,
    ScreenNameError,
    ScreenNameRefusal,
    StaffLabel,
    parse_screen_name,
    staff_label,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sanne", "Sanne"),
        ("  Sanne  ", "Sanne"),
        ("Jan   Willem", "Jan Willem"),
        ("Jan-Willem", "Jan-Willem"),
        ("O'Brien", "O'Brien"),
        ("O’Brien", "O’Brien"),
        ("Sam K.", "Sam K."),
        ("Zoë", "Zoë"),
        ("Łukasz", "Łukasz"),
        ("李雷", "李雷"),
        ("Жанна", "Жанна"),
        ("Team 7", "Team 7"),
        ("x" * MAX_LENGTH, "x" * MAX_LENGTH),
        ("xy", "xy"),
    ],
)
def test_a_screen_name_is_trimmed_and_may_hold_letters_digits_and_a_little_punctuation(
    text: str, expected: str
) -> None:
    assert parse_screen_name(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "\t\n", " "])
def test_an_empty_text_clears_the_name(text: str) -> None:
    assert parse_screen_name(text) is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("S", ScreenNameRefusal.TOO_SHORT),
        ("x" * (MAX_LENGTH + 1), ScreenNameRefusal.TOO_LONG),
        ("<b>Sanne</b>", ScreenNameRefusal.BAD_CHARACTER),
        ("Sanne\x00", ScreenNameRefusal.BAD_CHARACTER),
        ("Sanne\x1b[0m", ScreenNameRefusal.BAD_CHARACTER),
        ("Sanne‮", ScreenNameRefusal.BAD_CHARACTER),  # a right-to-left override
        ("Sanne​", ScreenNameRefusal.BAD_CHARACTER),  # a zero-width space
        ("Sa_nne", ScreenNameRefusal.BAD_CHARACTER),
        ("Sanne@work", ScreenNameRefusal.BAD_CHARACTER),
        ("Sanne\U0001f600", ScreenNameRefusal.BAD_CHARACTER),
        ("a{name}b", ScreenNameRefusal.BAD_CHARACTER),
    ],
)
def test_a_text_that_breaks_a_rule_is_refused_with_its_code_and_without_the_text(
    text: str, reason: ScreenNameRefusal
) -> None:
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name(text)

    assert refused.value.reason is reason
    assert refused.value.field == "screen_name"
    assert text.strip() not in str(refused.value) or len(text.strip()) < MIN_LENGTH


def test_the_characters_are_judged_before_the_length() -> None:
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name("<")  # one character, and not an allowed one

    assert refused.value.reason is ScreenNameRefusal.BAD_CHARACTER


def test_inner_white_space_is_folded_so_a_name_cannot_hide_behind_a_run_of_spaces() -> None:
    assert parse_screen_name("Sa nne") == "Sa nne"  # an em space is white space, not a letter


@pytest.mark.parametrize(
    ("mode", "screen_name", "account", "name"),
    [
        (StaffNameMode.SCREEN_NAME, "Sanne", "Sanne de Vries", "Sanne"),
        (StaffNameMode.SCREEN_NAME, None, "Sanne de Vries", None),  # never the account's name
        (StaffNameMode.SCREEN_NAME, "", "Sanne de Vries", None),
        (StaffNameMode.SCREEN_NAME, "Sanne", None, "Sanne"),
        (StaffNameMode.NAME, "Sanne", "Sanne de Vries", "Sanne de Vries"),
        (StaffNameMode.NAME, None, "Sanne de Vries", "Sanne de Vries"),
        (StaffNameMode.NAME, "Sanne", "", None),
        (StaffNameMode.TERM, "Sanne", "Sanne de Vries", None),
        (StaffNameMode.TERM, None, None, None),
    ],
)
def test_what_the_participant_is_told_for_every_setting_and_record(
    mode: StaffNameMode, screen_name: str | None, account: str | None, name: str | None
) -> None:
    assert staff_label(mode, screen_name, account) == StaffLabel(name)


def test_an_account_name_longer_than_a_screen_name_may_be_is_cut() -> None:
    label = staff_label(StaffNameMode.NAME, None, "x" * 100)

    assert label.name == "x" * MAX_LENGTH


def test_the_answer_of_the_endpoint_is_a_name_or_nothing() -> None:
    assert StaffLabel("Sanne").as_json() == {"name": "Sanne"}
    assert NO_LABEL.as_json() == {}
    assert StaffLabel("").as_json() == {}
