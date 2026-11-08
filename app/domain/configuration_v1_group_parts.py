"""Pure helpers of the group in a version-1 document (ADR 0099): items, schedule, reminders, form.

The group kind (``configuration_v1_group``) checks a whole group; the parts that read and write
the list fields (staffing rules and exceptions, reminder rules, custom form fields) are here. Each
is the rule one admin screen applies, as a function over plain JSON items, so the screens'
services can call it later. A list is checked as a whole, but an entry the current object already
holds is not checked again (``new_items``), as the portal checks an entry when it is added.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, time
from typing import cast

from app.domain.channels import ChannelKind, ReminderRule
from app.domain.configuration_checks import invalid, text_map
from app.domain.configuration_values import Json, Value, canonical_json
from app.domain.errors import ErrorCode
from app.domain.form_fields import Classification, FieldDefinition, FormDefinition
from app.domain.form_fields import FieldType as FormFieldType
from app.domain.messaging_service import MAX_REMINDER_RULES
from app.domain.scheduling import (
    Offer,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)


def items_of(value: Value | None) -> list[dict[str, Json]]:
    """Return the objects of a list field (empty when it is absent)."""
    return cast("list[dict[str, Json]]", value if isinstance(value, list) else [])


def same_item(item: Mapping[str, Json], others: Iterable[Mapping[str, Json]]) -> bool:
    """Whether an item equals one of ``others`` (compared as canonical JSON)."""
    text_ = canonical_json(dict(item))
    return any(canonical_json(dict(other)) == text_ for other in others)


def new_items(
    items: Sequence[dict[str, Json]], before: Sequence[dict[str, Json]]
) -> list[dict[str, Json]]:
    """Return the items a document adds: those the current object does not already hold.

    The portal checks an entry when it is added, not again when it is left alone, so an
    exception for a day that has passed or a reminder that no longer fits stays valid.
    """
    return [item for item in items if not same_item(item, before)]


def clock_of(value: Json, field: str) -> time:
    """Read a local time (``09:00``); anything else is refused naming ``field``."""
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        raise invalid(field) from None


def day_of(value: Json, field: str) -> date:
    """Read a date (``2026-12-25``); anything else is refused naming ``field``."""
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise invalid(field) from None


def clock_text(value: time) -> str:
    """Write a local time as ``HH:MM``, with seconds only when there are some."""
    return value.isoformat(timespec="minutes" if value.second == 0 else "seconds")


def _window(item: Mapping[str, Json]) -> TimeWindow:
    """Build the staffing block of a rule or an exception item."""
    return TimeWindow(
        clock_of(item["opens"], "opens"),
        clock_of(item["closes"], "closes"),
        int(cast("int", item["capacity"])),
        cast("str | None", item.get("label")),
        Offer(str(item.get("offer", Offer.SCHEDULED.value))),
        tuple(sorted({str(code) for code in cast("list[str]", item.get("subjects", []))})),
    )


def _rule(item: Mapping[str, Json]) -> ScheduleRule:
    """Build a weekly staffing rule from its item."""
    start, end = item.get("valid_from"), item.get("valid_to")
    return ScheduleRule(
        int(cast("int", item["weekday"])),
        _window(item),
        None if start is None else day_of(start, "valid_from"),
        None if end is None else day_of(end, "valid_to"),
    )


def _override(item: Mapping[str, Json]) -> ScheduleOverride:
    """Build an exception from its item: all three of opens, closes and capacity, or none."""
    day = day_of(item["day"], "day")
    parts = (item.get("opens"), item.get("closes"), item.get("capacity"))
    if all(part is None for part in parts):
        return ScheduleOverride(day, None)
    if any(part is None for part in parts):
        raise invalid("window")
    return ScheduleOverride(day, _window(item))


def rule_json(rule: ScheduleRule) -> dict[str, Json]:
    """Write a rule as the item the document and the export hold."""
    window = rule.window
    return {
        "weekday": rule.weekday,
        "opens": clock_text(window.opens),
        "closes": clock_text(window.closes),
        "capacity": window.capacity,
        "label": window.label,
        "offer": window.offer.value,
        "subjects": list(window.subjects),
        "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
        "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
    }


def override_json(override: ScheduleOverride) -> dict[str, Json]:
    """Write an exception as the item the document and the export hold."""
    window = override.window
    return {
        "day": override.day.isoformat(),
        "opens": None if window is None else clock_text(window.opens),
        "closes": None if window is None else clock_text(window.closes),
        "capacity": None if window is None else window.capacity,
        "label": None if window is None else window.label,
        "offer": Offer.SCHEDULED.value if window is None else window.offer.value,
        "subjects": [] if window is None else list(window.subjects),
    }


def schedule_of(
    fields: Mapping[str, Value],
) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
    """Return the rules and exceptions a group's fields hold.

    Raises:
        DomainError: ``invalid_configuration`` naming the offending part of an item.
    """
    rules = [_rule(item) for item in items_of(fields.get("schedule_rules"))]
    overrides = [_override(item) for item in items_of(fields.get("schedule_overrides"))]
    return rules, overrides


def check_reminders(
    items: Sequence[dict[str, Json]], before: Sequence[dict[str, Json]], phone_mode: PhoneMode
) -> list[dict[str, Json]]:
    """Check a group's reminder rules as ``MessagingService`` does; return them normalised.

    The offset, the number of rules and the pair of channel and offset are checked for all of
    them; the channel (email or SMS, and SMS only where the form asks for a phone number) only
    for a rule that is new, as the portal never removes an SMS rule when the phone field goes.

    Raises:
        DomainError: ``invalid_configuration`` naming ``minutes_before``, ``channel`` or
            ``reminders``; ``reminder_exists`` for the same channel and offset twice.
    """
    if len(items) > MAX_REMINDER_RULES:
        raise invalid("reminders")
    seen: set[tuple[str, int]] = set()
    rules: list[dict[str, Json]] = []
    for item in items:
        rule = ReminderRule(
            ChannelKind(str(item["channel"])),
            int(cast("int", item["minutes_before"])),
            bool(item["enabled"]),
        )
        pair = (rule.channel.value, rule.minutes_before)
        if pair in seen:
            raise invalid("minutes_before", ErrorCode.REMINDER_EXISTS)
        seen.add(pair)
        rules.append(
            {
                "channel": rule.channel.value,
                "minutes_before": rule.minutes_before,
                "enabled": rule.enabled,
            }
        )
    for new in new_items(rules, before):
        if new["channel"] == ChannelKind.SMS.value and phone_mode is PhoneMode.HIDDEN:
            raise invalid("channel")
    return rules


def _definition(raw: Mapping[str, Json]) -> FieldDefinition:
    """Build a form field from its item, which runs every check of the form screen."""
    options = raw.get("option_labels", {})
    if not isinstance(options, dict):
        raise invalid("options", ErrorCode.INVALID_FORM_FIELD)
    return FieldDefinition.create(
        key=str(raw["key"]),
        type=FormFieldType(str(raw["type"])),
        labels=text_map(raw["labels"], "label", code=ErrorCode.INVALID_FORM_FIELD),
        required=bool(raw.get("required", False)),
        classification=Classification(str(raw.get("classification", "pii"))),
        options=cast("list[str]", raw.get("options", [])),
        minimum=cast("int | None", raw.get("minimum")),
        maximum=cast("int | None", raw.get("maximum")),
        hints=text_map(raw.get("hints", {}), "hint", code=ErrorCode.INVALID_FORM_FIELD),
        option_labels={
            k: text_map(v, "options", code=ErrorCode.INVALID_FORM_FIELD) for k, v in options.items()
        },
    )


def _form_json(definition: FieldDefinition) -> dict[str, Json]:
    """Write a form field as the item the document and the export hold."""
    return {
        "key": definition.key,
        "type": definition.type.value,
        "required": definition.required,
        "classification": definition.classification.value,
        "options": list(definition.options),
        "minimum": definition.minimum,
        "maximum": definition.maximum,
        "labels": dict(definition.labels),
        "hints": dict(definition.hints),
        "option_labels": {k: dict(v) for k, v in definition.option_labels.items()},
        "retired": definition.retired,
    }


def check_form(
    items: Sequence[dict[str, Json]], before: Sequence[dict[str, Json]], limit: int
) -> list[dict[str, Json]]:
    """Check a group's custom form fields as ``FormService`` does; return them normalised.

    The listed fields are the form: the active ones in form order, the retired ones after. A
    field that exists now and is not listed is retired by the adapter, never deleted, so its
    key stays reserved. The type of an active field cannot change and a retired field cannot
    come back. A retired field left as it is is not checked again. The limit counts active
    fields and is lifted to what the form holds when the document adds none.

    Raises:
        DomainError: ``invalid_form_field`` naming the attribute, ``form_field_forbidden`` for a
            label that asks for forbidden data.
    """
    existing = {str(item["key"]): item for item in before}
    keys = [str(item["key"]) for item in items]
    if len(set(keys)) != len(keys):
        raise invalid("key", ErrorCode.INVALID_FORM_FIELD)
    active: list[FieldDefinition] = []
    retired: list[FieldDefinition] = []
    kept: list[dict[str, Json]] = []
    for item in items:
        old = existing.get(str(item["key"]))
        if item.get("retired") and old is not None and old.get("retired") and item == old:
            kept.append(item)
            continue
        if old is not None and not old.get("retired") and old["type"] != item["type"]:
            raise invalid("type", ErrorCode.INVALID_FORM_FIELD)
        if old is not None and old.get("retired") and not item.get("retired"):
            raise invalid("key", ErrorCode.INVALID_FORM_FIELD)
        (retired if item.get("retired") else active).append(_definition(item))
    was_active = {key for key, old in existing.items() if not old.get("retired")}
    added = [d for d in active if d.key not in was_active]
    form = FormDefinition.create(
        version=1,
        fields=active,
        max_custom_fields=limit if added else max(limit, len(active)),
        retired=retired,
    )
    return [
        *(_form_json(d) for d in form.fields),
        *(_form_json(d) for d in form.retired),
        *kept,
    ]
