"""The Channels report: messages, language mix and trial calls (ADR 0117 step 6, R2)."""

from datetime import UTC, date, datetime

from app.domain.channels import ChannelKind
from app.domain.usage import UsageCell
from app.domain.usage_report import ReportGroup
from app.domain.usage_report_channels import channels_report

GROUP = ReportGroup(1, 1, "balie", "Balie", "demo", "Demo", "Europe/Amsterdam")
SMALL = ReportGroup(2, 1, "intake", "Intake", "demo", "Demo", "Europe/Amsterdam")
FIRST = date(2026, 11, 2)
LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
HOUR = datetime(2026, 11, 4, 10, tzinfo=UTC)


def _message(
    count: int, purpose: str, state: str, channel: ChannelKind = ChannelKind.EMAIL
) -> UsageCell:
    return UsageCell(
        HOUR, 1, 1, "M1", count, 0, 0, channel_kind=channel, reason_code=f"{purpose}.{state}"
    )


def _report(cells: list[UsageCell], groups: list[ReportGroup] | None = None):  # type: ignore[no-untyped-def]
    return channels_report(cells, groups or [GROUP], FIRST, LAST, threshold=5, as_of=NOW)


def test_without_cells_the_report_is_empty() -> None:
    report = _report([])

    assert report.empty
    assert report.trial_joined_percent is None
    assert report.messages.failure_percent is None


def test_messages_are_counted_per_purpose_and_channel_with_a_failure_share() -> None:
    cells = [
        _message(60, "confirmation", "sent"),
        _message(40, "reminder", "sent"),
        _message(10, "reminder", "sent", ChannelKind.SMS),
        _message(10, "confirmation", "failed"),
        _message(10, "reminder", "failed"),
    ]

    messages = _report(cells).messages

    assert messages.by_purpose.rows[:2] == ("confirmation", "reminder")
    assert messages.by_purpose.cells[0] == (60, 10)
    assert messages.by_purpose.cells[1] == (50, 10)
    assert messages.by_purpose.column_totals == (110, 20)
    assert messages.failure_percent == round(100 * 20 / 130)
    assert messages.by_channel.rows == ("email", "sms", "whatsapp")


def test_a_small_failure_count_is_hidden_and_so_is_the_share() -> None:
    cells = [_message(60, "confirmation", "sent"), _message(2, "confirmation", "failed")]

    messages = _report(cells).messages

    assert messages.by_purpose.cells[0][1] is None
    assert messages.failure_percent is None


def test_a_purpose_unknown_to_the_screen_still_has_a_row() -> None:
    messages = _report([_message(30, "survey", "sent")]).messages

    assert "survey" in messages.by_purpose.rows


def test_a_message_code_without_a_state_is_ignored() -> None:
    cell = UsageCell(HOUR, 1, 1, "M1", 30, 0, 0, channel_kind=ChannelKind.EMAIL, reason_code="x")

    messages = _report([cell]).messages

    assert messages.by_purpose.column_totals == (None, None)


def _locale(count: int, locale: str, group: int = 1) -> UsageCell:
    return UsageCell(HOUR, 1, group, "M2", count, 0, 0, locale=locale)


def test_locales_are_cut_only_for_a_group_that_is_big_enough() -> None:
    cells = [
        _locale(40, "nl"),
        _locale(10, "en"),
        _locale(3, "en", group=2),
        _locale(7, "nl", group=2),
    ]

    report = _report(cells, [GROUP, SMALL])

    assert [(r.code, r.count) for r in report.locales.rows] == [("en", 10), ("nl", 40)]
    assert report.locales_groups_left_out == 1


def test_the_only_english_booking_of_a_small_group_is_not_visible() -> None:
    cells = [_locale(20, "nl", group=2), _locale(1, "en", group=2)]

    report = _report(cells, [GROUP, SMALL])

    assert report.locales.rows == ()
    assert report.locales_groups_left_out == 2


def _trial(count: int, step: str) -> UsageCell:
    return UsageCell(HOUR, 1, 1, "T1", count, 0, 0, reason_code=step)


def test_the_trial_funnel_has_a_share_for_each_step() -> None:
    cells = [
        _trial(100, "created"),
        _trial(60, "joined"),
        _trial(30, "entered"),
        _trial(5, "failed"),
    ]

    report = _report(cells)

    assert [r.code for r in report.trial.rows] == ["created", "joined", "entered", "failed"]
    assert report.trial_joined_percent == 60
    assert report.trial_entered_percent == 50


def test_a_funnel_step_below_the_threshold_has_no_share() -> None:
    cells = [_trial(100, "created"), _trial(60, "joined"), _trial(3, "entered")]

    assert _report(cells).trial_entered_percent is None
