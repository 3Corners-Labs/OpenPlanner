"""The group in a version-1 configuration document (ADR 0099): kind, rules and integrity.

A group is the biggest object of a document: its settings, texts, booking form, contacts,
holiday choice, staffing blocks, exceptions and reminders. Every rule here is the one the admin
screens apply: the kind's check builds the same ``Group`` dataclass the portal builds and the
same schedule, reminder and form objects, so a document cannot do anything the portal forbids.
The rules across objects (unique slugs, calendars that exist, a frozen time grid, an archived
group that stays read-only, a lower capacity than is booked) read what the document would
leave behind.

Rules that only live inside a service method today are small pure functions here; the services
may call them later (``_new_items`` for "only what is new is checked", ``check_reminders`` for
the reminder rules of ``MessagingService``, ``check_form`` for the form rules of
``FormService``).
"""

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, time
from typing import Any, cast

from app.domain.agenda import agents_for
from app.domain.agent_term import TERM_KEYS, TERM_PLURAL, TERM_SINGULAR, check_term_pair, clean_term
from app.domain.authorization import Permission
from app.domain.channel_settings import CONFIGURABLE_CHANNELS
from app.domain.channels import (
    MAX_REMINDER_MINUTES,
    MIN_REMINDER_MINUTES,
    ChannelKind,
    ReminderRule,
)
from app.domain.configuration_changes import (
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_checks import contacts_check
from app.domain.configuration_schema import (
    Anchor,
    Collection,
    FieldSpec,
    Kind,
    Shape,
    boolean,
    integer,
    json_object,
    objects,
    strings,
    text,
)
from app.domain.configuration_tree import Address, Entity, path_of, walk
from app.domain.configuration_values import Json, Value, canonical_json
from app.domain.entitlements import is_idp_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    Classification,
    FieldDefinition,
    FormDefinition,
)
from app.domain.form_fields import FieldType as FormFieldType
from app.domain.message_texts import clean_text
from app.domain.messaging_service import MAX_REMINDER_RULES
from app.domain.scheduling import (
    MAX_BOOKINGS_PER_EMAIL,
    MAX_CAPACITY,
    MAX_DIRECT_CALLS,
    MAX_DISPATCH_LEAD_MINUTES,
    MAX_LABEL_LENGTH,
    MAX_NAME_LENGTH,
    MAX_NOT_PICKED_UP_MINUTES,
    MAX_PARTICIPANT_CHANGE_LEAD_MINUTES,
    MAX_PLANNER_LEAD_MINUTES,
    MAX_QUEUE_VISIBLE_FROM_MINUTES,
    MAX_SLUG_LENGTH,
    MAX_TRIAL_CALL_MINUTES,
    MAX_TRIAL_CALLS_PER_BOOKING,
    MIN_TRIAL_CALL_MINUTES,
    RESERVED_SLUGS,
    DispatchKind,
    Group,
    JoinClient,
    Offer,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
    validate_schedule,
    windows_on,
)

DEFAULT_MAX_CUSTOM_FIELDS = 5
"""The number of custom form fields when the settings say nothing (ADR 0055)."""

_DEFAULTS = Group(0, 0, "default", "default", "UTC", 30)
"""A group with every default of the dataclass, the one source of a new group's defaults."""

_ENUMS: dict[str, type[PhoneMode] | type[DispatchKind] | type[JoinClient]] = {
    "phone_mode": PhoneMode,
    "dispatch_kind": DispatchKind,
    "join_client": JoinClient,
}
_SCHEDULE_FIELDS = (
    "schedule_rules",
    "schedule_overrides",
    "holiday_source",
    "holiday_calendars",
    "holiday_open_days",
)
"""The fields that decide when a group is staffed; a change to one can lower a capacity."""
_OFFERS = tuple(offer.value for offer in Offer)


def _default(name: str) -> object:
    """Return the dataclass default of a group setting, an enum as its value."""
    value = getattr(_DEFAULTS, name)
    return value.value if hasattr(value, "value") else value


def _setting(name: str, low: int, high: int | None = None, **rest: object) -> FieldSpec:
    """Describe a whole-number group setting with the dataclass default and the portal bounds."""
    return integer(name, default=_default(name), minimum=low, maximum=high, **rest)  # type: ignore[arg-type]


def _choice(name: str, choices: Iterable[str]) -> FieldSpec:
    """Describe a text group setting with a closed set and the dataclass default."""
    return text(name, default=_default(name), choices=tuple(choices))


_SETTINGS: tuple[FieldSpec, ...] = (
    text("name", required=True, max_length=MAX_NAME_LENGTH),
    text("timezone", required=True, max_length=64),
    integer("slot_minutes", required=True, minimum=5, maximum=480),
    _setting("min_notice_minutes", 0, 999999),
    _setting("horizon_days", 1, 366),
    _choice("phone_mode", (mode.value for mode in PhoneMode)),
    text("video_host_email", nullable=True),
    text("wxcc_business_address", nullable=True, max_length=255),
    text("wxcc_channel", nullable=True, max_length=255),
    text("wxcc_routing_value", nullable=True, max_length=512),
    _setting("not_picked_up_minutes", 1, MAX_NOT_PICKED_UP_MINUTES),
    integer("direct_call_max_concurrent", nullable=True, minimum=1, maximum=MAX_DIRECT_CALLS),
    _setting("dispatch_lead_minutes", 0, MAX_DISPATCH_LEAD_MINUTES),
    _choice("dispatch_kind", (kind.value for kind in DispatchKind)),
    _setting("participant_change_lead_minutes", 0, MAX_PARTICIPANT_CHANGE_LEAD_MINUTES),
    _setting("planner_change_min_lead_minutes", 0, MAX_PLANNER_LEAD_MINUTES),
    boolean("trial_call_enabled", default=_default("trial_call_enabled")),
    _setting("trial_call_minutes", MIN_TRIAL_CALL_MINUTES, MAX_TRIAL_CALL_MINUTES),
    _setting("trial_calls_per_booking", 1, MAX_TRIAL_CALLS_PER_BOOKING),
    _choice("join_client", (client.value for client in JoinClient)),
    boolean("embedding_enabled", default=_default("embedding_enabled")),
    _setting("queue_visible_from_minutes", 0, MAX_QUEUE_VISIBLE_FROM_MINUTES),
    boolean("queue_free_pick_allowed", default=_default("queue_free_pick_allowed")),
    _setting("max_bookings_per_email", 1, MAX_BOOKINGS_PER_EMAIL),
    text("calendar_invite_address", nullable=True, max_length=254),
)
"""The group's own settings: every field of the dataclass the portal's settings form edits."""

_GROUP_ARGUMENTS = tuple(spec.name for spec in _SETTINGS)

_FORM_ITEMS = (
    text("key", required=True, max_length=40),
    text("type", required=True, choices=tuple(kind.value for kind in FormFieldType)),
    boolean("required", default=False),
    text("classification", default="pii", choices=tuple(c.value for c in Classification)),
    strings("options"),
    integer("minimum", nullable=True),
    integer("maximum", nullable=True),
    json_object("labels"),
    json_object("hints", default={}),
    json_object("option_labels", default={}),
    boolean("retired", default=False),
)
_RULE_ITEMS = (
    integer("weekday", required=True, minimum=0, maximum=6),
    text("opens", required=True),
    text("closes", required=True),
    integer("capacity", required=True, minimum=0, maximum=MAX_CAPACITY),
    text("label", nullable=True, max_length=MAX_LABEL_LENGTH),
    text("offer", default=Offer.SCHEDULED.value, choices=_OFFERS),
    text("valid_from", nullable=True),
    text("valid_to", nullable=True),
)
_OVERRIDE_ITEMS = (
    text("day", required=True),
    text("opens", nullable=True),
    text("closes", nullable=True),
    integer("capacity", nullable=True, minimum=0, maximum=MAX_CAPACITY),
    text("label", nullable=True, max_length=MAX_LABEL_LENGTH),
    text("offer", default=Offer.SCHEDULED.value, choices=_OFFERS),
)
_REMINDER_ITEMS = (
    text("channel", required=True, choices=tuple(c.value for c in CONFIGURABLE_CHANNELS)),
    integer(
        "minutes_before", required=True, minimum=MIN_REMINDER_MINUTES, maximum=MAX_REMINDER_MINUTES
    ),
    boolean("enabled", default=True),
)

_GROUP_TABLES = frozenset(
    {
        "groups",
        "group_contacts",
        "group_form_fields",
        "group_holiday_calendars",
        "group_holiday_open_days",
        "schedule_rules",
        "schedule_overrides",
        "reminder_rules",
    }
)


def _invalid(field: str, code: ErrorCode = ErrorCode.INVALID_CONFIGURATION) -> DomainError:
    """Return the refusal that names ``field``."""
    return DomainError(code, field)


def _items(value: Value | None) -> list[dict[str, Json]]:
    """Return the objects of a list field (empty when it is absent)."""
    return cast("list[dict[str, Json]]", value if isinstance(value, list) else [])


def _strings(fields: Mapping[str, Value], name: str) -> list[str]:
    """Return the texts of a list field (empty when it is absent)."""
    found = fields.get(name)
    return [str(x) for x in found] if isinstance(found, list) else []


def _same_item(item: Mapping[str, Json], others: Iterable[Mapping[str, Json]]) -> bool:
    """Whether an item equals one of ``others`` (compared as canonical JSON)."""
    text_ = canonical_json(dict(item))
    return any(canonical_json(dict(other)) == text_ for other in others)


def _new_items(
    items: Sequence[dict[str, Json]], before: Sequence[dict[str, Json]]
) -> list[dict[str, Json]]:
    """Return the items a document adds: those the current object does not already hold.

    The portal checks an entry when it is added, not again when it is left alone, so an
    exception for a day that has passed or a reminder that no longer fits stays valid.
    """
    return [item for item in items if not _same_item(item, before)]


def _clock(value: Json, field: str) -> time:
    """Read a local time (``09:00``); anything else is refused naming ``field``."""
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        raise _invalid(field) from None


def _day(value: Json, field: str) -> date:
    """Read a date (``2026-12-25``); anything else is refused naming ``field``."""
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _invalid(field) from None


def _clock_text(value: time) -> str:
    """Write a local time as ``HH:MM``, with seconds only when there are some."""
    return value.isoformat(timespec="minutes" if value.second == 0 else "seconds")


def _build_group(view: Mapping[str, Value], ctx: RuleContext) -> Group:
    """Build the group the fields describe, which validates every bound of the portal."""
    values: dict[str, Any] = {name: view[name] for name in _GROUP_ARGUMENTS if name in view}
    for name, enum in _ENUMS.items():
        if name in values:
            values[name] = enum(values[name])
    return Group(
        0,
        0,
        slug=str(view["slug"]),
        idp_key=str(view.get("idp_key") or ""),
        archived_at=ctx.now if view.get("archived") else None,
        **values,
    )


def _window(item: Mapping[str, Json]) -> TimeWindow:
    """Build the staffing block of a rule or an exception item."""
    return TimeWindow(
        _clock(item["opens"], "opens"),
        _clock(item["closes"], "closes"),
        int(cast("int", item["capacity"])),
        cast("str | None", item.get("label")),
        Offer(str(item.get("offer", Offer.SCHEDULED.value))),
    )


def _rule(item: Mapping[str, Json]) -> ScheduleRule:
    """Build a weekly staffing rule from its item."""
    start, end = item.get("valid_from"), item.get("valid_to")
    return ScheduleRule(
        int(cast("int", item["weekday"])),
        _window(item),
        None if start is None else _day(start, "valid_from"),
        None if end is None else _day(end, "valid_to"),
    )


def _override(item: Mapping[str, Json]) -> ScheduleOverride:
    """Build an exception from its item: all three of opens, closes and capacity, or none."""
    day = _day(item["day"], "day")
    parts = (item.get("opens"), item.get("closes"), item.get("capacity"))
    if all(part is None for part in parts):
        return ScheduleOverride(day, None)
    if any(part is None for part in parts):
        raise _invalid("window")
    return ScheduleOverride(day, _window(item))


def _rule_json(rule: ScheduleRule) -> dict[str, Json]:
    """Write a rule as the item the document and the export hold."""
    window = rule.window
    return {
        "weekday": rule.weekday,
        "opens": _clock_text(window.opens),
        "closes": _clock_text(window.closes),
        "capacity": window.capacity,
        "label": window.label,
        "offer": window.offer.value,
        "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
        "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
    }


def _override_json(override: ScheduleOverride) -> dict[str, Json]:
    """Write an exception as the item the document and the export hold."""
    window = override.window
    return {
        "day": override.day.isoformat(),
        "opens": None if window is None else _clock_text(window.opens),
        "closes": None if window is None else _clock_text(window.closes),
        "capacity": None if window is None else window.capacity,
        "label": None if window is None else window.label,
        "offer": Offer.SCHEDULED.value if window is None else window.offer.value,
    }


def schedule_of(
    fields: Mapping[str, Value],
) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
    """Return the rules and exceptions a group's fields hold.

    Raises:
        DomainError: ``invalid_configuration`` naming the offending part of an item.
    """
    rules = [_rule(item) for item in _items(fields.get("schedule_rules"))]
    overrides = [_override(item) for item in _items(fields.get("schedule_overrides"))]
    return rules, overrides


def _check_schedule(
    view: Mapping[str, Value],
    before: Mapping[str, Value] | None,
    group: Group,
    ctx: RuleContext,
) -> dict[str, list[dict[str, Json]]]:
    """Check the staffing rules and exceptions; return them normalised.

    An exception for a day before the group's own today is refused when it is new.
    """
    rules, overrides = schedule_of(view)
    validate_schedule(rules, overrides)
    items = [_override_json(o) for o in overrides]
    had = _items(before.get("schedule_overrides")) if before else []
    today = group.today(ctx.now)
    for new in _new_items(items, had):
        if _day(new["day"], "day") < today:
            raise _invalid("day")
    return {
        "schedule_rules": [_rule_json(rule) for rule in rules],
        "schedule_overrides": items,
    }


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
        raise _invalid("reminders")
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
            raise _invalid("minutes_before", ErrorCode.REMINDER_EXISTS)
        seen.add(pair)
        rules.append(
            {
                "channel": rule.channel.value,
                "minutes_before": rule.minutes_before,
                "enabled": rule.enabled,
            }
        )
    for new in _new_items(rules, before):
        if new["channel"] == ChannelKind.SMS.value and phone_mode is PhoneMode.HIDDEN:
            raise _invalid("channel")
    return rules


def _texts(value: Json, field: str) -> dict[str, str]:
    """Return a JSON object of texts, or refuse naming ``field``."""
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise _invalid(field, ErrorCode.INVALID_FORM_FIELD)
    return dict(cast("dict[str, str]", value))


def _definition(raw: Mapping[str, Json]) -> FieldDefinition:
    """Build a form field from its item, which runs every check of the form screen."""
    options = raw.get("option_labels", {})
    if not isinstance(options, dict):
        raise _invalid("options", ErrorCode.INVALID_FORM_FIELD)
    return FieldDefinition.create(
        key=str(raw["key"]),
        type=FormFieldType(str(raw["type"])),
        labels=_texts(raw["labels"], "label"),
        required=bool(raw.get("required", False)),
        classification=Classification(str(raw.get("classification", "pii"))),
        options=cast("list[str]", raw.get("options", [])),
        minimum=cast("int | None", raw.get("minimum")),
        maximum=cast("int | None", raw.get("maximum")),
        hints=_texts(raw.get("hints", {}), "hint"),
        option_labels={k: _texts(v, "options") for k, v in options.items()},
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
        raise _invalid("key", ErrorCode.INVALID_FORM_FIELD)
    active: list[FieldDefinition] = []
    retired: list[FieldDefinition] = []
    kept: list[dict[str, Json]] = []
    for item in items:
        old = existing.get(str(item["key"]))
        if item.get("retired") and old is not None and old.get("retired") and item == old:
            kept.append(item)
            continue
        if old is not None and not old.get("retired") and old["type"] != item["type"]:
            raise _invalid("type", ErrorCode.INVALID_FORM_FIELD)
        if old is not None and old.get("retired") and not item.get("retired"):
            raise _invalid("key", ErrorCode.INVALID_FORM_FIELD)
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


def _check_group(
    view: Mapping[str, Value], current: Mapping[str, Value] | None, ctx: RuleContext
) -> Mapping[str, Value]:
    """Check a whole group as the settings, staffing, reminder and form screens do."""
    if current is None and str(view["slug"]) in RESERVED_SLUGS:
        raise _invalid("slug", ErrorCode.SLUG_TAKEN)
    group = _build_group(view, ctx)
    idp_key = view.get("idp_key")
    if idp_key is not None and not is_idp_key(str(idp_key)):
        raise _invalid("idp_key")
    days = [
        _day(day, "holiday_open_days").isoformat() for day in _strings(view, "holiday_open_days")
    ]
    before = current or {}
    checked: dict[str, Value] = {**view, "holiday_open_days": cast("list[Json]", days)}
    checked.update(cast("Mapping[str, Value]", _check_schedule(view, current, group, ctx)))
    checked["reminder_rules"] = cast(
        "list[Json]",
        check_reminders(
            _items(view.get("reminder_rules")),
            _items(before.get("reminder_rules")),
            group.phone_mode,
        ),
    )
    limit = ctx.settings.get("booking_form_max_custom_fields", DEFAULT_MAX_CUSTOM_FIELDS)
    checked["form_fields"] = cast(
        "list[Json]",
        check_form(
            _items(view.get("form_fields")),
            _items(before.get("form_fields")),
            limit if isinstance(limit, int) else DEFAULT_MAX_CUSTOM_FIELDS,
        ),
    )
    return checked


def _check_text(
    view: Mapping[str, Value], current: Mapping[str, Value] | None, ctx: RuleContext
) -> Mapping[str, Value]:
    """Check one text of a group: a known locale, a known block, the portal's own text rules."""
    if view["locale"] not in ctx.locales:
        raise _invalid("locale")
    key, body = str(view["key"]), str(view["text"])
    cleaned = clean_term(key, body) if key in TERM_KEYS else clean_text(key, body)
    return {**view, "text": cleaned}


GROUP_TEXT = Kind(
    "group_text",
    key=("locale", "key"),
    permission=Permission.TEXTS_MANAGE,
    tables=frozenset({"group_texts"}),
    fields=(
        text("locale", required=True, max_length=8),
        text("key", required=True, max_length=64),
        text("text", required=True, max_length=2000),
    ),
    check=_check_text,
    description="A group's own text of one message block in one locale.",
)

GROUP = Kind(
    "group",
    key=("slug",),
    permission=Permission.GROUP_EDIT,
    create_permission=Permission.GROUP_CREATE,
    delete_permission=Permission.GROUP_DELETE,
    anchor=Anchor.GROUP,
    tables=_GROUP_TABLES,
    fields=(
        text("slug", required=True, max_length=MAX_SLUG_LENGTH),
        *_SETTINGS,
        text("idp_key", nullable=True, permission=Permission.IDP_MAPPING_MANAGE),
        text(
            "holiday_source",
            default="inherit",
            choices=("inherit", "own"),
            permission=Permission.SCHEDULE_MANAGE,
        ),
        strings("holiday_calendars", permission=Permission.SCHEDULE_MANAGE),
        strings(
            "holiday_open_days",
            permission=Permission.SCHEDULE_MANAGE,
            columns=("group_holiday_open_days.day",),
        ),
        boolean(
            "archived", default=False, permission=Permission.GROUP_DELETE, columns=("archived_at",)
        ),
        objects(
            "form_fields",
            items=_FORM_ITEMS,
            ordered=True,
            columns=tuple(
                f"group_form_fields.{name}"
                for name in (
                    "key",
                    "position",
                    "type",
                    "required",
                    "classification",
                    "options",
                    "minimum",
                    "maximum",
                    "labels",
                    "hints",
                    "option_labels",
                    "retired",
                )
            ),
        ),
        objects(
            "schedule_rules",
            items=_RULE_ITEMS,
            permission=Permission.SCHEDULE_MANAGE,
            columns=tuple(
                f"schedule_rules.{name}"
                for name in (
                    "weekday",
                    "opens",
                    "closes",
                    "capacity",
                    "label",
                    "offer",
                    "valid_from",
                    "valid_to",
                )
            ),
        ),
        objects(
            "schedule_overrides",
            items=_OVERRIDE_ITEMS,
            permission=Permission.SCHEDULE_MANAGE,
            columns=tuple(
                f"schedule_overrides.{name}"
                for name in ("day", "opens", "closes", "capacity", "label", "offer")
            ),
        ),
        objects(
            "reminder_rules",
            items=_REMINDER_ITEMS,
            permission=Permission.REMINDERS_MANAGE,
            columns=tuple(
                f"reminder_rules.{name}" for name in ("channel", "minutes_before", "enabled")
            ),
        ),
        json_object(
            "contacts",
            nullable=True,
            permission=Permission.CLOSURE_MANAGE,
            check=contacts_check,
            columns=("group_contacts.body",),
        ),
    ),
    collections=(Collection("texts", GROUP_TEXT, Shape.MAP),),
    check=_check_group,
    derived=frozenset({"groups.form_version"}),
    description="A bookable team: its settings, form, staffing, reminders, texts and contacts.",
)


# --- rules across objects -----------------------------------------------------------------


def _days(values: Iterable[object]) -> list[date]:
    """Return the dates among ``values``; what is no date was already refused by the kind."""
    found: list[date] = []
    for value in values:
        try:
            found.append(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return found


def _group_path(address: Address) -> str:
    """Return the path of a group in a document from its address (organisation, slug)."""
    return path_of(path_of("", "organisations", address[:1]), "groups", address[1:])


def _groups(root: Entity) -> dict[Address, Entity]:
    """Return every group of a tree by address."""
    return {address: group for address, group in walk(root, GROUP.name)}


def _calendar_days(root: Entity) -> dict[str, set[date]]:
    """Return the holidays of every calendar of a tree by calendar slug."""
    days: dict[str, set[date]] = {}
    for address, calendar in walk(root, "holiday_calendar"):
        found = days.setdefault(address[0], set())
        for holiday in calendar.children.get("days", ()):
            try:
                found.add(date.fromisoformat(holiday.key[0]))
            except ValueError:
                continue
    return days


def _organisation_calendars(root: Entity, slug: str) -> list[str]:
    """Return the calendar slugs an organisation of a tree has chosen."""
    for address, organisation in walk(root, "organisation"):
        if address == (slug,):
            return [
                str(x) for x in cast("list[str]", organisation.fields.get("holiday_calendars", []))
            ]
    return []


def _closed_days(root: Entity, address: Address, group: Entity) -> set[date]:
    """Return the holidays on which a group is closed: its effective calendars, minus open days."""
    days = _calendar_days(root)
    own = group.fields.get("holiday_source") == "own"
    slugs = (
        _strings(group.fields, "holiday_calendars")
        if own
        else (_organisation_calendars(root, address[0]))
    )
    closed: set[date] = set()
    for slug in slugs:
        closed |= days.get(str(slug), set())
    return closed - set(_days(_strings(group.fields, "holiday_open_days")))


def _agents(
    root: Entity,
    address: Address,
    group: Entity,
    load_start: datetime,
    load_end: datetime,
    ctx: RuleContext,
) -> int | None:
    """Return the agents on duty for a whole slot under a tree's schedule, or ``None``."""
    try:
        rules, overrides = schedule_of(group.fields)
        built = _build_group({"slug": address[-1], **group.fields}, ctx)
    except (DomainError, ValueError, KeyError, TypeError):
        return None
    day = load_start.astimezone(built.tz).date()
    if day in _closed_days(root, address, group) and not any(o.day == day for o in overrides):
        overrides = [*overrides, ScheduleOverride(day, None)]
    windows = windows_on(day, rules, overrides)
    return agents_for(built, load_start, load_end, day, windows)


def _slug_rules(resolution: Resolution) -> Iterator[Refusal]:
    """Slugs are unique across the installation; an idp value is unique within an organisation."""
    groups = _groups(resolution.resulting)
    touched = {change.address for change in resolution.changes if change.kind == GROUP.name}
    slugs = Counter(address[-1] for address in groups)
    keys: Counter[tuple[str, str]] = Counter(
        (address[0], str(group.fields.get("idp_key") or address[-1]))
        for address, group in groups.items()
    )
    for address, group in groups.items():
        if address not in touched:
            continue
        if slugs[address[-1]] > 1:
            yield Refusal(_group_path(address), ErrorCode.SLUG_TAKEN, "slug")
        if keys[(address[0], str(group.fields.get("idp_key") or address[-1]))] > 1:
            yield Refusal(_group_path(address), ErrorCode.IDP_KEY_TAKEN, "idp_key")


def _calendar_rules(resolution: Resolution) -> Iterator[Refusal]:
    """Every holiday calendar a group names exists, and a new open day is a real holiday."""
    known = _calendar_days(resolution.resulting)
    before = _groups(resolution.current)
    for address, group in _groups(resolution.resulting).items():
        path = _group_path(address)
        named = _strings(group.fields, "holiday_calendars")
        if any(slug not in known for slug in named):
            yield Refusal(path, ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND, "holiday_calendars")
        old = before.get(address)
        had = set(_days(_strings(old.fields, "holiday_open_days"))) if old else set()
        added = [d for d in _days(_strings(group.fields, "holiday_open_days")) if d not in had]
        if not added:
            continue
        closed = _calendar_days_for(resolution.resulting, address, group)
        today = _today(group, resolution.context)
        if any(d not in closed or (today is not None and d < today) for d in added):
            yield Refusal(path, ErrorCode.HOLIDAY_NOT_FOUND, "holiday_open_days")


def _calendar_days_for(root: Entity, address: Address, group: Entity) -> set[date]:
    """Return every holiday date of the calendars a group observes (open days included)."""
    own = group.fields.get("holiday_source") == "own"
    slugs = (
        _strings(group.fields, "holiday_calendars")
        if own
        else (_organisation_calendars(root, address[0]))
    )
    days = _calendar_days(root)
    return {d for slug in slugs for d in days.get(str(slug), set())}


def _today(group: Entity, ctx: RuleContext) -> date | None:
    """Return the group's local today, or ``None`` when its fields cannot build a group."""
    try:
        return _build_group({"slug": "x", **group.fields}, ctx).today(ctx.now)
    except (DomainError, ValueError, KeyError, TypeError):
        return None


def _grid_and_archive_rules(resolution: Resolution) -> Iterator[Refusal]:
    """The time grid freezes while bookings are ahead; archiving and un-archiving follow rules."""
    before = _groups(resolution.current)
    for change in resolution.changes:
        if change.kind != GROUP.name:
            continue
        old = before.get(change.address)
        busy = change.address in resolution.usage.future_bookings
        if change.action is ChangeAction.UPDATE and busy:
            for name in ("slot_minutes", "timezone"):
                if name in change.fields:
                    yield Refusal(change.path, ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, name)
        if "archived" not in change.fields:
            continue
        was = bool(old and old.fields.get("archived"))
        now = bool(change.values.get("archived"))
        if now and not was and busy:
            yield Refusal(change.path, ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, "archived")
        if was and not now:
            yield Refusal(change.path, ErrorCode.GROUP_ARCHIVED, "archived")


def _archived_rules(resolution: Resolution) -> Iterator[Refusal]:
    """An archived group, and everything in it, is read-only; only its deletion may pass."""
    archived = {
        (address[0], address[-1])
        for address, group in _groups(resolution.current).items()
        if group.fields.get("archived")
    }
    for change in (*resolution.changes, *resolution.deletions):
        if (change.organisation, change.group) not in archived:
            continue
        if change.kind == GROUP.name and (
            change.action is ChangeAction.DELETE or "archived" in change.fields
        ):
            continue
        yield Refusal(change.path, ErrorCode.GROUP_ARCHIVED)


def _capacity_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A change to the staffing may not create or worsen a shortfall on a slot already booked.

    The portal lets a planner lower the staffing and shows the shortfall (ADR 0044); a document
    changes many groups at once, so it is refused instead (ADR 0099 point 7).
    """
    before = _groups(resolution.current)
    after = _groups(resolution.resulting)
    ctx = resolution.context
    for change in resolution.changes:
        if change.kind != GROUP.name or not set(change.fields) & set(_SCHEDULE_FIELDS):
            continue
        old, new = before.get(change.address), after.get(change.address)
        if old is None or new is None:
            continue
        for load in resolution.usage.slot_loads.get(change.address, ()):
            now = _agents(resolution.resulting, change.address, new, load.start, load.end, ctx)
            was = _agents(resolution.current, change.address, old, load.start, load.end, ctx)
            if now is not None and was is not None and now < load.booked and now < was:
                yield Refusal(
                    change.path, ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED, "schedule_rules"
                )
                break


def _term_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A group sets both words of the agent term in a locale, or neither."""
    for address, group in _groups(resolution.resulting).items():
        words: dict[str, dict[str, str | None]] = {}
        for entry in group.children.get("texts", ()):
            if entry.key[1] in TERM_KEYS:
                words.setdefault(entry.key[0], {TERM_SINGULAR: None, TERM_PLURAL: None})[
                    entry.key[1]
                ] = str(entry.fields.get("text"))
        for locale, pair in words.items():
            try:
                check_term_pair(pair)
            except DomainError as error:
                yield Refusal(
                    path_of(_group_path(address), "texts", (locale, str(error.field))),
                    error.code,
                    error.field,
                )


INTEGRITY: tuple[IntegrityRule, ...] = (
    _slug_rules,
    _calendar_rules,
    _grid_and_archive_rules,
    _archived_rules,
    _capacity_rules,
    _term_rules,
)
"""The rules across objects that concern groups."""

__all__ = ["GROUP", "GROUP_TEXT", "INTEGRITY", "check_form", "check_reminders", "schedule_of"]
