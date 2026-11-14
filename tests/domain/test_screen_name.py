"""What a screen name may be and what the participant is told of the staff member (ADR 0123 5c)."""

import re
import unicodedata
from pathlib import Path

import pytest

from app.domain.guest_access import StaffNameMode
from app.domain.screen_name import (
    MAX_LENGTH,
    MAX_MARKS,
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


# ---- normalisation, marks and the need for a letter (a review of step 5c) ---------------------


def test_a_decomposed_and_a_composed_spelling_are_one_name_of_one_length() -> None:
    composed = "Zo\u00eb"
    decomposed = "Zoe\u0308"

    assert parse_screen_name(decomposed) == parse_screen_name(composed) == composed
    assert len(parse_screen_name(decomposed) or "") == 3


def test_the_length_is_counted_after_the_normalisation() -> None:
    forty_decomposed = "e\u0301" * MAX_LENGTH  # 80 code points, 40 characters once composed
    too_many = "e\u0301" * (MAX_LENGTH + 1)

    assert parse_screen_name(forty_decomposed) == "\u00e9" * MAX_LENGTH
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name(too_many)
    assert refused.value.reason is ScreenNameRefusal.TOO_LONG


@pytest.mark.parametrize(
    "text",
    [
        "\u0301\u0302Sam",  # two bare marks at the start
        "Sam \u0301Tom",  # a mark after a space
        "Sam-\u0301",  # a mark after punctuation
        "a" + "\u0301" * (MAX_MARKS + 2),  # a long run (the first one composes with the letter)
        "a" + "\u0300\u0301\u0302\u0303\u0304\u0305\u0306\u0307",
        "1\u0301\u0301",  # a mark after a digit
    ],
)
def test_a_mark_must_follow_a_letter_and_a_run_of_marks_is_short(text: str) -> None:
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name(text)

    assert refused.value.reason is ScreenNameRefusal.BAD_MARKS


@pytest.mark.parametrize("text", ["a\u0323\u0302", "\u1ebf", "\u0928\u093f"])
def test_up_to_two_marks_after_a_letter_are_fine_also_in_other_scripts(text: str) -> None:
    assert parse_screen_name(text + "m") is not None


@pytest.mark.parametrize("text", ["..", "- -", "'.'", " . . "])
def test_a_name_of_punctuation_alone_is_refused(text: str) -> None:
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name(text)

    assert refused.value.reason is ScreenNameRefusal.NO_LETTER


def test_a_name_of_digits_alone_is_a_name() -> None:
    assert parse_screen_name("42") == "42"


@pytest.mark.parametrize("joiner", ["\u200d", "\u200c", "\u200b", "\u2060", "\u202e", "\u2066"])
def test_zero_width_and_direction_characters_stay_refused_also_inside_a_name(joiner: str) -> None:
    with pytest.raises(ScreenNameError) as refused:
        parse_screen_name(f"Sa{joiner}nne")

    assert refused.value.reason is ScreenNameRefusal.BAD_CHARACTER


def test_the_stored_name_is_always_nfc() -> None:
    for text in ("Ame\u0301lie", "Jos\u00e9", "Zoe\u0308"):
        stored = parse_screen_name(text)
        assert stored is not None
        assert unicodedata.is_normalized("NFC", stored)


def test_the_longest_name_is_the_same_number_in_the_rule_the_column_and_the_script() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "app/web/static/call-poll.js").read_text()
    models = (root / "app/adapters/db/models.py").read_text()
    migration = (root / "migrations/versions/0118_staff_screen_name.py").read_text()

    in_script = re.search(r"var MAX_NAME = (\d+);", script)
    in_column = re.search(
        r"screen_name: Mapped\[str \| None\] = mapped_column\(String\((\d+)\)\)", models
    )
    in_migration = re.search(r'"screen_name", sa\.String\(length=(\d+)\)', migration)

    assert in_script
    assert in_column
    assert in_migration
    assert {int(in_script[1]), int(in_column[1]), int(in_migration[1])} == {MAX_LENGTH}
