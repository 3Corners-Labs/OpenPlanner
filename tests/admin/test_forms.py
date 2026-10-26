"""Reading admin form values: dd-mm-yyyy dates and 24-hour times, whatever the browser locale."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.admin.forms import Field, FormData
from app.i18n import Translator, load_catalogues

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _form(values: dict[str, str]) -> FormData:
    return FormData(values, Translator(load_catalogues()).bind("nl"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("09:00", time(9)), ("9:05", time(9, 5)), ("17.30", time(17, 30)), ("23:59", time(23, 59))],
)
def test_clock_reads_a_24_hour_time(raw: str, expected: time) -> None:
    form = _form({"opens": raw})

    assert form.clock("opens") == expected
    assert form.errors == []


@pytest.mark.parametrize("raw", ["", "24:00", "9:5", "12:00 PM", "09:00:00", "noon"])
def test_clock_refuses_anything_else_with_the_hh_mm_message(raw: str) -> None:
    form = _form({"opens": raw})

    assert form.clock("opens") is None
    assert [(e.field, e.message) for e in form.errors] == [("opens", "Vul een tijd in als uu:mm.")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03-04-2026", date(2026, 4, 3)),
        ("3-4-2026", date(2026, 4, 3)),
        ("03/04/2026", date(2026, 4, 3)),
        ("3.4.2026", date(2026, 4, 3)),
        ("2026-04-03", date(2026, 4, 3)),  # ISO, as in links and older bookmarks
    ],
)
def test_day_reads_day_month_year(raw: str, expected: date) -> None:
    form = _form({"holiday_day": raw})

    assert form.day("holiday_day") == expected
    assert form.errors == []


@pytest.mark.parametrize("raw", ["", "31-02-2026", "04/03/26", "April 3", "2026-4-3"])
def test_day_refuses_anything_else_with_the_format_message(raw: str) -> None:
    form = _form({"holiday_day": raw})

    assert form.day("holiday_day") is None
    # The parse message, not the field's domain message ("already has a holiday").
    assert [(e.field, e.message) for e in form.errors] == [
        ("holiday_day", "Vul een datum in als dd-mm-jjjj.")
    ]


@pytest.mark.parametrize(
    ("value", "shown"),
    [("2026-04-03", "03-04-2026"), ("3-4-2026", "3-4-2026"), ("", ""), ("onzin", "onzin")],
)
def test_date_field_shows_iso_values_as_day_month_year(value: str, shown: str) -> None:
    assert Field("day", "Datum", "date", value).date_text == shown


def test_moment_joins_day_and_time_in_the_team_zone() -> None:
    form = _form({"ends_at": "01-07-2026", "ends_at_time": "18:30"})

    assert form.optional_moment("ends_at", AMSTERDAM) == datetime(2026, 7, 1, 16, 30, tzinfo=UTC)
    assert form.moment_value("ends_at") == "01-07-2026T18:30"


def test_moment_is_none_when_both_parts_are_empty() -> None:
    form = _form({"ends_at": "", "ends_at_time": ""})

    assert form.optional_moment("ends_at", AMSTERDAM) is None
    assert form.errors == []


@pytest.mark.parametrize(
    "values",
    [
        {"ends_at": "2026-07-01", "ends_at_time": ""},
        {"ends_at": "", "ends_at_time": "18:30"},
        {"ends_at": "morgen", "ends_at_time": "18:30"},
        {"ends_at": "2026-07-01", "ends_at_time": "6:30 PM"},
    ],
)
def test_moment_needs_a_valid_day_and_time(values: dict[str, str]) -> None:
    form = _form(values)

    assert form.optional_moment("ends_at", AMSTERDAM) is None
    assert [(e.field, e.message) for e in form.errors] == [
        ("ends_at", "Vul een datum (dd-mm-jjjj) en tijd (uu:mm) in.")
    ]


def test_moment_value_keeps_the_submitted_parts_or_falls_back() -> None:
    assert _form({"ends_at_time": "8:00"}).moment_value("ends_at") == "T8:00"
    assert _form({}).moment_value("ends_at", "2026-01-03T00:00") == "2026-01-03T00:00"
