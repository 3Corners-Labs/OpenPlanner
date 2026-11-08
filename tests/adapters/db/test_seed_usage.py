"""The made-up usage cells of the demo group fill every report family (ADR 0117 step 6)."""

from datetime import UTC, date, datetime

from app.adapters.db.seed_usage import usage_example_cells
from app.domain.usage_report import ReportGroup
from app.domain.usage_report_capacity import capacity_report
from app.domain.usage_report_channels import channels_report
from app.domain.usage_report_queue import queue_report

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
GROUP = ReportGroup(7, 3, "demo", "Demo", "demo", "Demo", "Europe/Amsterdam")
FIRST, LAST = date(2026, 10, 27), date(2026, 11, 8)


def test_the_example_cells_belong_to_the_group_and_fill_the_three_families() -> None:
    cells = usage_example_cells(3, 7, NOW)

    assert {(c.organisation_id, c.group_id) for c in cells} == {(3, 7)}
    args = (cells, [GROUP], FIRST, LAST)
    assert not queue_report(*args, threshold=5, as_of=NOW).empty
    capacity = capacity_report(*args, threshold=5, as_of=NOW)
    assert not capacity.empty
    assert capacity.heat.offered
    assert capacity.heat.hours
    assert not channels_report(*args, threshold=5, as_of=NOW).empty


def test_one_day_is_small_enough_to_show_a_hidden_day() -> None:
    capacity = capacity_report(
        usage_example_cells(3, 7, NOW), [GROUP], FIRST, LAST, threshold=5, as_of=NOW
    )

    shown = [
        d.slots is not None
        for d in capacity.days
        if d.slots is not None or d.day >= date(2026, 10, 31)
    ]
    assert any(
        d.slots is None for d in capacity.days if date(2026, 10, 31) <= d.day <= date(2026, 11, 8)
    )
    assert any(shown)
