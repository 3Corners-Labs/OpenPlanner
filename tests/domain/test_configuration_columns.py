"""The helper that finds configuration columns no kind covers (ADR 0099 point 3)."""

from app.domain.configuration_schema import Kind, boolean, text
from tests.support.configuration_columns import uncovered_columns


def test_a_column_nobody_names_is_reported() -> None:
    kind = Kind(
        "calendar",
        key=("slug",),
        tables=frozenset({"holiday_calendars"}),
        fields=(text("slug", required=True),),
    )

    assert uncovered_columns([kind]) == {"holiday_calendars": {"name"}}


def test_a_field_a_column_list_and_a_derived_entry_each_cover() -> None:
    named = Kind(
        "calendar",
        key=("slug",),
        tables=frozenset({"holiday_calendars"}),
        fields=(text("slug", required=True), text("label", columns=("name",))),
    )
    derived = Kind(
        "calendar",
        key=("slug",),
        tables=frozenset({"holiday_calendars"}),
        fields=(text("slug", required=True),),
        derived=frozenset({"holiday_calendars.name"}),
    )

    assert uncovered_columns([named]) == {}
    assert uncovered_columns([derived]) == {}


def test_a_qualified_column_applies_to_its_table_only() -> None:
    kind = Kind(
        "calendar",
        key=("slug",),
        tables=frozenset({"holiday_calendars", "holidays"}),
        fields=(
            text("slug", required=True),
            text("label", columns=("holiday_calendars.name",)),
            boolean("flag", columns=("holidays.day",)),
        ),
    )

    assert uncovered_columns([kind]) == {"holidays": {"name"}}


def test_surrogate_ids_and_foreign_keys_need_no_field() -> None:
    kind = Kind(
        "holiday",
        key=("day",),
        tables=frozenset({"holidays"}),
        fields=(text("day", required=True), text("name", required=True)),
    )

    assert uncovered_columns([kind]) == {}
