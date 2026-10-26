from datetime import UTC, datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

from app.admin.timezones import DEFAULT_TIMEZONE, timezone_options


def test_default_is_amsterdam_and_listed_first() -> None:
    options = timezone_options(DEFAULT_TIMEZONE)

    assert DEFAULT_TIMEZONE == "Europe/Amsterdam"
    assert options[0].value == "Europe/Amsterdam"
    assert [o.value for o in options].count("Europe/Amsterdam") == 1


def test_only_region_zones_and_utc_are_offered() -> None:
    values = {o.value for o in timezone_options(DEFAULT_TIMEZONE)}

    assert {"Europe/Brussels", "America/Curacao", "UTC"} <= values
    assert not values & {"CET", "GB", "Etc/GMT+1", "EST5EDT"}


def test_labels_read_without_underscores() -> None:
    labels = {o.value: o.label for o in timezone_options(DEFAULT_TIMEZONE)}

    assert labels["America/Argentina/Buenos_Aires"] == "America/Argentina/Buenos Aires"


def test_a_stored_zone_outside_the_list_stays_selectable() -> None:
    options = timezone_options("Etc/GMT-1")

    assert options[0].value == "Etc/GMT-1"


def test_an_unknown_submitted_value_is_not_echoed() -> None:
    values = [o.value for o in timezone_options("<script>alert(1)</script>")]

    assert "<script>alert(1)</script>" not in values
    assert values[0] == DEFAULT_TIMEZONE


def _offset_hours(moment: datetime, zone: str) -> float:
    offset = moment.astimezone(ZoneInfo(zone)).utcoffset()
    assert offset is not None
    return offset.total_seconds() / 3600


def test_the_default_zone_switches_summer_and_winter_time() -> None:
    # Amsterdam switches at 01:00 UTC on the last Sundays of March and October.
    zone = timezone_options(DEFAULT_TIMEZONE)[0].value

    assert _offset_hours(datetime(2026, 3, 29, 0, 59, tzinfo=UTC), zone) == 1
    assert _offset_hours(datetime(2026, 3, 29, 1, 0, tzinfo=UTC), zone) == 2
    assert _offset_hours(datetime(2026, 10, 25, 0, 59, tzinfo=UTC), zone) == 2
    assert _offset_hours(datetime(2026, 10, 25, 1, 0, tzinfo=UTC), zone) == 1


def test_the_pinned_tz_database_is_installed() -> None:
    # The image reads zones only from the tzdata package (Dockerfile: PYTHONTZPATH="").
    assert files("tzdata.zoneinfo").joinpath("Europe", "Amsterdam").is_file()
