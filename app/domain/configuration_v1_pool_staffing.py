"""The staffing of a counted capacity pool in a configuration document (ADR 0122 step 3).

A counted pool carries its ``timezone`` and its staffing: weekly blocks (``staffing_rules``) and
per-date exceptions (``staffing_overrides``), shaped like a service's schedule with ``units``
(people counted) where a service has ``capacity``, and without subjects (a pool serves several
services). The list items are read and written by the helpers of the group's schedule
(``configuration_v1_group_parts``), so a block means the same in both places. The rules are the
domain's: ``check_timezone`` and ``check_pool_staffing`` run on what the document leaves behind,
and a refusal names the field of the document, never a value.

A document is read as recorded: a block or exception in the past is no error, and lowering the
units below what is already committed is no refusal either (decision P3: existing appointments
stay and show as a shortage, new ones stop at once), unlike a service's staffing, whose lowering
a document refuses (ADR 0099 point 7). A document without these fields leaves a pool's staffing
as it is; naming ``staffing_rules`` (even an empty list) replaces the weekly blocks as a whole.
"""

from collections.abc import Mapping
from typing import cast
from zoneinfo import ZoneInfo

from app.domain.configuration_checks import invalid
from app.domain.configuration_schema import FieldSpec, integer, objects, text
from app.domain.configuration_v1_group_parts import (
    items_of,
    override_json,
    override_of,
    rule_json,
    rule_of,
)
from app.domain.configuration_values import Json, Value, canonical_json
from app.domain.errors import DomainError
from app.domain.pool_staffing import PoolStaffing, check_pool_staffing, check_timezone
from app.domain.routing import MAX_LABEL_LENGTH
from app.domain.scheduling import MAX_CAPACITY, Offer, ScheduleOverride, ScheduleRule

_OFFERS = tuple(offer.value for offer in Offer)
_FIELD_OF = {"schedule_rules": "staffing_rules", "schedule_overrides": "staffing_overrides"}
"""The name a refusal of the domain's check gets in the document."""

RULE_ITEMS = (
    integer("weekday", required=True, minimum=0, maximum=6),
    text("opens", required=True),
    text("closes", required=True),
    integer("units", required=True, minimum=0, maximum=MAX_CAPACITY),
    text("label", nullable=True, max_length=MAX_LABEL_LENGTH),
    text("offer", default=Offer.SCHEDULED.value, choices=_OFFERS),
    text("valid_from", nullable=True),
    text("valid_to", nullable=True),
)
"""The items of ``staffing_rules``: when and how many, and what the block offers (P7)."""
OVERRIDE_ITEMS = (
    text("day", required=True),
    text("opens", nullable=True),
    text("closes", nullable=True),
    integer("units", nullable=True, minimum=0, maximum=MAX_CAPACITY),
    text("label", nullable=True, max_length=MAX_LABEL_LENGTH),
    text("offer", default=Offer.SCHEDULED.value, choices=_OFFERS),
)
"""The items of ``staffing_overrides``: a date and a window, or no window for a closed day."""

STAFFING_COLUMNS = (
    "capacity_pools.timezone",
    "pool_staffing_rules.weekday",
    "pool_staffing_rules.opens",
    "pool_staffing_rules.closes",
    "pool_staffing_rules.units",
    "pool_staffing_rules.label",
    "pool_staffing_rules.offer",
    "pool_staffing_rules.valid_from",
    "pool_staffing_rules.valid_to",
    "pool_staffing_overrides.day",
    "pool_staffing_overrides.opens",
    "pool_staffing_overrides.closes",
    "pool_staffing_overrides.units",
    "pool_staffing_overrides.label",
    "pool_staffing_overrides.offer",
)
"""Every column the three staffing fields cover, as ``table.column``."""

STAFFING_TABLES = frozenset({"pool_staffing_rules", "pool_staffing_overrides"})


def staffing_fields() -> tuple[FieldSpec, ...]:
    """Return the three fields of a counted pool's staffing: time zone, blocks and exceptions."""
    return (
        text(
            "timezone",
            nullable=True,
            max_length=64,
            columns=("capacity_pools.timezone",),
            description="IANA time zone of the blocks; a pool with staffing needs one.",
        ),
        objects(
            "staffing_rules",
            items=RULE_ITEMS,
            columns=tuple(c for c in STAFFING_COLUMNS if c.startswith("pool_staffing_rules.")),
            description="Weekly blocks: weekday, local opens and closes, units, offer.",
        ),
        objects(
            "staffing_overrides",
            items=OVERRIDE_ITEMS,
            columns=tuple(c for c in STAFFING_COLUMNS if c.startswith("pool_staffing_overrides.")),
            description="Exceptions per date: a window with units, or none for a closed day.",
        ),
    )


def _item(source: Mapping[str, Json]) -> dict[str, Json]:
    """Return a pool item as the group's schedule helpers read it: ``units`` is ``capacity``."""
    return {**source, "capacity": source.get("units")}


def _unit_item(written: dict[str, Json]) -> dict[str, Json]:
    """Return a group-shaped item as a pool item: ``capacity`` becomes ``units``, no subjects."""
    written.pop("subjects", None)
    capacity = written.pop("capacity")
    return {**written, "units": capacity}


def rule_item(rule: ScheduleRule) -> dict[str, Json]:
    """Write a block as the item the document and the export hold."""
    return _unit_item(rule_json(rule))


def override_item(override: ScheduleOverride) -> dict[str, Json]:
    """Write an exception as the item the document and the export hold."""
    return _unit_item(override_json(override))


def _rule_order(item: dict[str, Json]) -> tuple[int, str, str, str]:
    """Order blocks by weekday, then opening time, closing time and validity (a total order)."""
    return (
        cast("int", item["weekday"]),
        cast("str", item["opens"]),
        cast("str", item["closes"]),
        canonical_json(item),
    )


def _override_order(item: dict[str, Json]) -> tuple[str, str, str]:
    """Order exceptions by date, then opening time (a closed day has none)."""
    return (cast("str", item["day"]), cast("str | None", item["opens"]) or "", canonical_json(item))


def sorted_items(
    rules: list[ScheduleRule], overrides: list[ScheduleOverride]
) -> tuple[list[Json], list[Json]]:
    """Return blocks and exceptions as items in the one order a document and an export use.

    One order makes the same staffing the same document however it was entered, so an import of
    an export plans nothing.
    """
    return (
        cast("list[Json]", sorted((rule_item(r) for r in rules), key=_rule_order)),
        cast("list[Json]", sorted((override_item(o) for o in overrides), key=_override_order)),
    )


def staffing_of(fields: Mapping[str, Value]) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
    """Return the blocks and exceptions a pool's fields hold, as the domain reads them.

    Raises:
        DomainError: ``invalid_configuration`` naming the offending part of an item
            (``opens``, ``closes``, ``day``, ``window``, ...).
    """
    rules = [rule_of(_item(item)) for item in items_of(fields.get("staffing_rules"))]
    overrides = [override_of(_item(item)) for item in items_of(fields.get("staffing_overrides"))]
    return rules, overrides


def check_staffing(fields: Mapping[str, Value]) -> dict[str, Value]:
    """Check a counted pool's time zone and staffing; return the three fields normalised.

    Raises:
        DomainError: ``invalid_configuration`` naming ``timezone`` (unknown, or missing while the
            pool has blocks), ``staffing_rules`` or ``staffing_overrides`` (overlap, too many),
            or the part of an item that cannot be read.
    """
    name = fields.get("timezone")
    rules, overrides = staffing_of(fields)
    if name is None:
        if rules or overrides:
            raise invalid("timezone")
        zone: ZoneInfo | None = None
    else:
        zone = ZoneInfo(check_timezone(str(name)))
    if zone is not None:
        try:
            check_pool_staffing(PoolStaffing(zone, tuple(rules), tuple(overrides)))
        except DomainError as error:
            raise invalid(_FIELD_OF.get(error.field or "", error.field or "")) from None
    ordered_rules, ordered_overrides = sorted_items(rules, overrides)
    return {
        "timezone": None if name is None else str(name),
        "staffing_rules": ordered_rules,
        "staffing_overrides": ordered_overrides,
    }
