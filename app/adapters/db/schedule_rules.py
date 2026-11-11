"""The one mapping of a weekly rule row to the domain ``ScheduleRule``."""

from app.adapters.db.models import ScheduleRuleRow
from app.adapters.db.routing_json import subjects_from_column
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow


def rule_from_row(row: ScheduleRuleRow) -> ScheduleRule:
    """Map a weekly rule row, with its validity dates and id, to the domain."""
    window = TimeWindow(
        row.opens,
        row.closes,
        row.capacity,
        row.label,
        Offer(row.offer),
        subjects_from_column(row.subjects),
    )
    return ScheduleRule(row.weekday, window, row.valid_from, row.valid_to, id=row.id)
