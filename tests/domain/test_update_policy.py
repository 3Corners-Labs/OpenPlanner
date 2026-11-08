"""Line, window, support and stop of updates (ADR 0116 points 2, 6 and 9)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.update_policy import (
    LineStatus,
    SupportLine,
    auto_apply_effective,
    claimable,
    line_support,
    on_line,
    parse_support,
    parse_window,
    upgrade_stop,
    valid_line,
    valid_window,
)
from app.domain.updates import (
    ListedRelease,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
    Version,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
# Monday 2026-01-05, 01:30 UTC = 02:30 in Amsterdam (CET).
MONDAY_0230 = datetime(2026, 1, 5, 1, 30, tzinfo=UTC)


def listed(raw: str) -> ListedRelease:
    version = Version.parse(raw)
    assert version is not None
    return ListedRelease(version, f"https://github.com/acme/openplanner/releases/tag/v{raw}")


def request(**overrides: object) -> UpdateRequest:
    fields: dict[str, object] = {
        "id": 1,
        "action": UpdateAction.UPDATE,
        "target": Version(1, 4, 1),
        "from_version": Version(1, 4, 0),
        "requested_by": "s-1",
        "requested_at": MONDAY_0230,
        "status": UpdateStatus.REQUESTED,
    }
    fields.update(overrides)
    return UpdateRequest(**fields)  # type: ignore[arg-type]


# Line.


def test_a_line_is_empty_or_minor() -> None:
    assert valid_line("")
    assert valid_line("1.4")
    assert not valid_line("1.4.0")
    assert not valid_line("v1.4")
    assert not valid_line("01.4")


def test_a_line_keeps_the_patches_of_its_minor_only() -> None:
    listing = [listed("1.5.0"), listed("1.4.2"), listed("1.4.1-rc.1"), listed("1.3.9")]

    assert [str(i.version) for i in on_line(listing, "1.4")] == ["1.4.2", "1.4.1-rc.1"]
    assert on_line(listing, "") == listing


# Window.


def test_a_window_is_up_to_seven_ranges_of_at_least_half_an_hour() -> None:
    assert valid_window("")
    assert valid_window("mon 02:00-04:00, SAT 01:00-01:30")
    assert not valid_window("mon 02:00-02:15")
    assert not valid_window("mon 04:00-02:00")
    assert not valid_window("monday 02:00-04:00")
    assert not valid_window("mon 25:00-26:00")
    assert not valid_window(", ".join(["tue 01:00-02:00"] * 8))


def test_the_window_is_open_inside_a_range_in_the_installations_zone() -> None:
    window = parse_window("mon 02:00-04:00", AMSTERDAM)

    assert window.is_open(MONDAY_0230)
    assert not window.is_open(MONDAY_0230 + timedelta(hours=2))  # 04:30 local
    assert not window.is_open(MONDAY_0230 + timedelta(days=1))
    assert parse_window("", AMSTERDAM).is_open(MONDAY_0230)


def test_the_next_opening_is_now_inside_and_the_next_range_outside() -> None:
    window = parse_window("mon 02:00-04:00, thu 22:00-23:30", AMSTERDAM)

    assert window.next_opening(MONDAY_0230) == MONDAY_0230
    after = MONDAY_0230 + timedelta(hours=3)  # Monday 05:30 local
    assert window.next_opening(after) == datetime(2026, 1, 8, 21, 0, tzinfo=UTC)
    friday = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
    assert window.next_opening(friday) == datetime(2026, 1, 12, 1, 0, tzinfo=UTC)
    assert parse_window("", AMSTERDAM).next_opening(friday) is None


def test_auto_apply_works_in_production_only_with_a_window() -> None:
    window = parse_window("mon 02:00-04:00", AMSTERDAM)
    no_window = parse_window("", AMSTERDAM)

    assert auto_apply_effective(True, no_window, production=False)
    assert auto_apply_effective(True, window, production=True)
    assert not auto_apply_effective(True, no_window, production=True)
    assert not auto_apply_effective(False, window, production=True)


# Claim.


def test_a_click_is_claimed_at_once_and_an_automatic_request_in_the_window() -> None:
    window = parse_window("mon 02:00-04:00", AMSTERDAM)
    later = MONDAY_0230 + timedelta(hours=3)

    assert claimable(request(), later, window)
    assert claimable(request(requested_by="system"), MONDAY_0230, window)
    assert not claimable(request(requested_by="system"), later, window)


def test_a_request_waits_for_its_apply_after() -> None:
    window = parse_window("", AMSTERDAM)
    waiting = request(apply_after=MONDAY_0230 + timedelta(hours=1))

    assert not claimable(waiting, MONDAY_0230, window)
    assert claimable(waiting, MONDAY_0230 + timedelta(hours=1), window)
    assert not claimable(request(status=UpdateStatus.RUNNING), MONDAY_0230, window)


# Support.


SUPPORT = """{"policy": "N-1 from 1.0.0", "lines": [
  {"line": "1.5", "status": "supported", "until": null},
  {"line": "1.4", "status": "security", "until": null},
  {"line": "1.2", "status": "extended", "until": "2027-12-31"},
  {"line": "1.1", "status": "retired"},
  {"line": "x", "status": "ended"},
  "nonsense"
]}"""


def test_support_json_names_each_lines_status() -> None:
    lines = parse_support(SUPPORT)

    assert lines == [
        SupportLine("1.5", LineStatus.SUPPORTED),
        SupportLine("1.4", LineStatus.SECURITY),
        SupportLine("1.2", LineStatus.EXTENDED, "2027-12-31"),
    ]
    with pytest.raises(ValueError, match="lines"):
        parse_support('{"policy": "x"}')


def test_the_running_lines_status_and_an_unlisted_line_has_ended() -> None:
    lines = parse_support(SUPPORT)

    assert line_support(lines, Version(1, 4, 3)) == SupportLine("1.4", LineStatus.SECURITY)
    assert line_support(lines, Version(1, 3, 0)) == SupportLine("1.3", LineStatus.ENDED)
    assert line_support(lines, None) is None
    assert line_support([], Version(1, 4, 3)) is None


# Stop.


def test_a_database_older_than_the_stop_must_upgrade_through_it_first() -> None:
    stop = Version(1, 0, 0)

    assert upgrade_stop(Version(1, 2, 0), Version(0, 9, 4), stop) == "1.0.0"
    assert upgrade_stop(Version(1, 2, 0), Version(1, 0, 3), stop) == ""
    assert upgrade_stop(Version(0, 6, 0), Version(0, 5, 1), stop) == ""  # before the stop
    assert upgrade_stop(Version(1, 2, 0), None, stop) == ""  # a new database
    assert upgrade_stop(None, Version(0, 9, 0), stop) == ""  # no release build
    assert upgrade_stop(Version(1, 2, 0), Version(0, 9, 0), None) == ""
