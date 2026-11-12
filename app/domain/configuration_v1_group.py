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
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from typing import Any, cast

from app.domain.agenda import agents_for
from app.domain.agent_term import TERM_KEYS, clean_term
from app.domain.authorization import Permission
from app.domain.channel_settings import CONFIGURABLE_CHANNELS
from app.domain.channels import (
    MAX_REMINDER_MINUTES,
    MIN_REMINDER_MINUTES,
)
from app.domain.configuration_changes import (
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_checks import (
    contacts_check,
    effective_idp_key,
    int_setting,
    invalid,
    strings_of,
    term_pair_refusals,
    text_override_check,
)
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
from app.domain.configuration_v1_calendars import (
    CalendarWorld,
    changes_holidays,
    parse_days,
    world_of,
)
from app.domain.configuration_v1_group_parts import (
    check_form,
    check_reminders,
    day_of,
    items_of,
    new_items,
    override_json,
    rule_json,
    schedule_of,
)
from app.domain.configuration_v1_names import (
    GROUP_KIND,
    GROUP_TEXT_KIND,
)
from app.domain.configuration_values import Json, Value
from app.domain.contact_center_codec import contact_center_from_json, contact_center_to_json
from app.domain.entitlements import is_idp_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    Classification,
)
from app.domain.form_fields import FieldType as FormFieldType
from app.domain.message_texts import clean_text
from app.domain.routing import RESERVED_FROM_NOW
from app.domain.routing_codec import routing_from_json, routing_to_json
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
    MAX_RESCHEDULE_AUTO_DELAY_MINUTES,
    MAX_RESCHEDULE_AUTO_MAX_BOOKINGS,
    MAX_SLOT_MINUTES,
    MAX_SLUG_LENGTH,
    MAX_TRIAL_CALL_MINUTES,
    MAX_TRIAL_CALLS_PER_BOOKING,
    MIN_RESCHEDULE_AUTO_DELAY_MINUTES,
    MIN_SLOT_MINUTES,
    MIN_TRIAL_CALL_MINUTES,
    RESERVED_SLUGS,
    DispatchFallback,
    DispatchKind,
    Group,
    JoinClient,
    Offer,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    validate_schedule,
    windows_on,
)

_DEFAULTS = Group(0, 0, "default", "default", "UTC", 30)
"""A group with every default of the dataclass, the one source of a new group's defaults."""

_ENUMS: dict[
    str, type[PhoneMode] | type[DispatchKind] | type[DispatchFallback] | type[JoinClient]
] = {
    "phone_mode": PhoneMode,
    "dispatch_kind": DispatchKind,
    "dispatch_fallback": DispatchFallback,
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
    integer("slot_minutes", required=True, minimum=MIN_SLOT_MINUTES, maximum=MAX_SLOT_MINUTES),
    _setting("min_notice_minutes", 0, 999999),
    _setting("horizon_days", 1, 366),
    _choice("phone_mode", (mode.value for mode in PhoneMode)),
    text("video_host_email", nullable=True),
    _setting("not_picked_up_minutes", 1, MAX_NOT_PICKED_UP_MINUTES),
    integer("direct_call_max_concurrent", nullable=True, minimum=1, maximum=MAX_DIRECT_CALLS),
    _setting("dispatch_lead_minutes", 0, MAX_DISPATCH_LEAD_MINUTES),
    _choice("dispatch_kind", (kind.value for kind in DispatchKind)),
    _choice("dispatch_fallback", (kind.value for kind in DispatchFallback)),
    text("fallback_space_room_id", nullable=True, max_length=200),
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
    boolean("reschedule_auto_send", default=_default("reschedule_auto_send")),
    _setting(
        "reschedule_auto_delay_minutes",
        MIN_RESCHEDULE_AUTO_DELAY_MINUTES,
        MAX_RESCHEDULE_AUTO_DELAY_MINUTES,
    ),
    _setting("reschedule_auto_max_bookings", 1, MAX_RESCHEDULE_AUTO_MAX_BOOKINGS),
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
    strings("subjects"),
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
    strings("subjects"),
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
        "group_contact_center",
        "group_contacts",
        "group_form_fields",
        "group_holiday_calendars",
        "group_holiday_open_days",
        "schedule_rules",
        "schedule_overrides",
        "reminder_rules",
    }
)


def _check_contact_center(value: Value, ctx: RuleContext) -> Value:
    """Check a group's contact-center routing against the platform's descriptor; normalise it."""
    return cast(
        "Json", contact_center_to_json(contact_center_from_json(value, ctx.contact_centers))
    )


def _check_routing(value: Value, ctx: RuleContext) -> Value:
    """Check a group's routing as the routing screens do; return it as it is stored.

    A mapping onto a name OpenPlanner sends itself since ADR 0121 step 4b (``RESERVED_FROM_NOW``)
    is refused, as the mapping form refuses it.

    Raises:
        DomainError: ``invalid_configuration`` (``variable_name``) for such a name.
    """
    routing = routing_from_json(value)
    if any(variable.name in RESERVED_FROM_NOW for _, variable in routing.mapping):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "variable_name")
    return cast("Json", routing_to_json(routing))


def _build_group(view: Mapping[str, Value], ctx: RuleContext) -> Group:
    """Build the group the fields describe, which validates every bound of the portal."""
    values: dict[str, Any] = {name: view[name] for name in _GROUP_ARGUMENTS if name in view}
    for name, enum in _ENUMS.items():
        if name in values:
            values[name] = enum(values[name])
    routing = view.get("routing")
    if routing is not None:
        values["routing"] = routing_from_json(routing)
    values["contact_center"] = contact_center_from_json(
        view.get("contact_center"), ctx.contact_centers
    )
    return Group(
        0,
        0,
        slug=str(view["slug"]),
        idp_key=str(view.get("idp_key") or ""),
        archived_at=ctx.now if view.get("archived") else None,
        **values,
    )


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
    items = [override_json(o) for o in overrides]
    had = items_of(before.get("schedule_overrides")) if before else []
    today = group.today(ctx.now)
    for new in new_items(items, had):
        if day_of(new["day"], "day") < today:
            raise invalid("day")
    return {
        "schedule_rules": [rule_json(rule) for rule in rules],
        "schedule_overrides": items,
    }


def _check_group(
    view: Mapping[str, Value], current: Mapping[str, Value] | None, ctx: RuleContext
) -> Mapping[str, Value]:
    """Check a whole group as the settings, staffing, reminder and form screens do."""
    if current is None and str(view["slug"]) in RESERVED_SLUGS:
        raise invalid("slug", ErrorCode.SLUG_TAKEN)
    group = _build_group(view, ctx)
    idp_key = view.get("idp_key")
    if idp_key is not None and not is_idp_key(str(idp_key)):
        raise invalid("idp_key")
    days = [
        day_of(day, "holiday_open_days").isoformat()
        for day in strings_of(view, "holiday_open_days")
    ]
    before = current or {}
    checked: dict[str, Value] = {**view, "holiday_open_days": cast("list[Json]", days)}
    checked.update(cast("Mapping[str, Value]", _check_schedule(view, current, group, ctx)))
    checked["reminder_rules"] = cast(
        "list[Json]",
        check_reminders(
            items_of(view.get("reminder_rules")),
            items_of(before.get("reminder_rules")),
            group.phone_mode,
        ),
    )
    checked["form_fields"] = cast(
        "list[Json]",
        check_form(
            items_of(view.get("form_fields")),
            items_of(before.get("form_fields")),
            int_setting(ctx, "booking_form_max_custom_fields"),
        ),
    )
    return checked


def _clean_group_text(key: str, body: str) -> str:
    """Clean one text of a group as the portal does: a term word or a message block."""
    return clean_term(key, body) if key in TERM_KEYS else clean_text(key, body)


_check_text = text_override_check(_clean_group_text)


GROUP_TEXT = Kind(
    GROUP_TEXT_KIND,
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
    GROUP_KIND,
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
                    "subjects",
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
                for name in ("day", "opens", "closes", "capacity", "label", "offer", "subjects")
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
            "contact_center",
            nullable=True,
            check=_check_contact_center,
            columns=(
                "wxcc_business_address",
                "wxcc_channel",
                "wxcc_routing_value",
                "group_contact_center.kind",
                "group_contact_center.routing_value",
                "group_contact_center.attributes",
            ),
            description="The platform the group dispatches to, its routing value and attributes.",
        ),
        json_object(
            "routing",
            nullable=True,
            check=_check_routing,
            description="Subjects, switched-on routing attributes and their mapping.",
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
    derived=frozenset({"groups.form_version", "groups.fallback_active_since"}),
    description="A bookable team: its settings, form, staffing, reminders, texts and contacts.",
)


# --- rules across objects -----------------------------------------------------------------


def _group_path(address: Address) -> str:
    """Return the path of a group in a document from its address (organisation, slug)."""
    return path_of(path_of("", "organisations", address[:1]), "groups", address[1:])


def _groups(root: Entity) -> dict[Address, Entity]:
    """Return every group of a tree by address."""
    return {address: group for address, group in walk(root, GROUP_KIND)}


def _slug_rules(resolution: Resolution) -> Iterator[Refusal]:
    """Slugs are unique across the installation; an idp value is unique within an organisation."""
    groups = _groups(resolution.resulting)
    touched = {change.address for change in resolution.changes if change.kind == GROUP_KIND}
    slugs = Counter(address[-1] for address in groups)
    keys: Counter[tuple[str, str]] = Counter(
        (address[0], effective_idp_key(group.fields, address[-1]))
        for address, group in groups.items()
    )
    for address, group in groups.items():
        if address not in touched:
            continue
        if slugs[address[-1]] > 1:
            yield Refusal(_group_path(address), ErrorCode.SLUG_TAKEN, "slug")
        if keys[(address[0], effective_idp_key(group.fields, address[-1]))] > 1:
            yield Refusal(_group_path(address), ErrorCode.IDP_KEY_TAKEN, "idp_key")


def _built(fields: Mapping[str, Value], address: Address, ctx: RuleContext) -> Group:
    """Build the group a tree holds at ``address``; its own refusal when its fields do not."""
    return _build_group({"slug": address[-1], **fields}, ctx)


def _open_day_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A new open day is a holiday of the calendars the group observes and not in the past."""
    before = _groups(resolution.current)
    world = world_of(resolution.resulting)
    for address, group in _groups(resolution.resulting).items():
        old = before.get(address)
        had = set(parse_days(strings_of(old.fields, "holiday_open_days"))) if old else set()
        added = [
            d for d in parse_days(strings_of(group.fields, "holiday_open_days")) if d not in had
        ]
        if not added:
            continue
        path = _group_path(address)
        try:
            today = _built(group.fields, address, resolution.context).today(resolution.context.now)
        except DomainError as error:
            yield Refusal(path, error.code, error.field)
            continue
        observed = world.observed(address)
        if any(d not in observed or d < today for d in added):
            yield Refusal(path, ErrorCode.HOLIDAY_NOT_FOUND, "holiday_open_days")


def _grid_and_archive_rules(resolution: Resolution) -> Iterator[Refusal]:
    """The time grid freezes while bookings are ahead; archiving and un-archiving follow rules."""
    before = _groups(resolution.current)
    for change in resolution.changes:
        if change.kind != GROUP_KIND:
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
        if change.kind == GROUP_KIND and (
            change.action is ChangeAction.DELETE or "archived" in change.fields
        ):
            continue
        yield Refusal(change.path, ErrorCode.GROUP_ARCHIVED)


def _agents(
    world: CalendarWorld,
    address: Address,
    staffing: tuple[Group, list[ScheduleRule], list[ScheduleOverride]],
    start: datetime,
    end: datetime,
) -> int:
    """Return the agents on duty for a whole slot under a schedule and the holidays it sees."""
    group, rules, overrides = staffing
    day = start.astimezone(group.tz).date()
    if day in world.closed(address) and not any(o.day == day for o in overrides):
        overrides = [*overrides, ScheduleOverride(day, None)]
    return agents_for(group, start, end, day, windows_on(day, rules, overrides))


def _staffing(
    fields: Mapping[str, Value], address: Address, ctx: RuleContext
) -> tuple[Group, list[ScheduleRule], list[ScheduleOverride]]:
    """Build a group and read its schedule; a group that cannot be built is a refusal."""
    rules, overrides = schedule_of(fields)
    return _built(fields, address, ctx), rules, overrides


def _capacity_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A change to the staffing may not create or worsen a shortfall on a slot already booked.

    The portal lets a planner lower the staffing and shows the shortfall (ADR 0044); a document
    changes many groups at once, so it is refused instead (ADR 0099 point 7). A holiday is
    staffing too: a day closed by a calendar, by the calendars an organisation chose or by a
    group's holiday fields removes capacity from groups the plan does not otherwise touch, so
    then every group with booked slots is checked.
    """
    changed = {
        c.address
        for c in resolution.changes
        if c.kind == GROUP_KIND and set(c.fields) & set(_SCHEDULE_FIELDS)
    }
    targets = changed | (
        set(resolution.usage.slot_loads) if changes_holidays(resolution) else set()
    )
    if not any(resolution.usage.slot_loads.get(address) for address in targets):
        return
    before, after = _groups(resolution.current), _groups(resolution.resulting)
    old_world, new_world = world_of(resolution.current), world_of(resolution.resulting)
    for address in sorted(targets):
        old, new = before.get(address), after.get(address)
        loads = resolution.usage.slot_loads.get(address, ())
        if old is None or new is None or not loads:
            continue
        path = _group_path(address)
        try:
            was = _staffing(old.fields, address, resolution.context)
            now = _staffing(new.fields, address, resolution.context)
        except DomainError as error:
            yield Refusal(path, error.code, error.field)
            continue
        for load in loads:
            after_agents = _agents(new_world, address, now, load.start, load.end)
            before_agents = _agents(old_world, address, was, load.start, load.end)
            if after_agents < load.booked and after_agents < before_agents:
                yield Refusal(path, ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED, "schedule_rules")
                break


def _term_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A group sets both words of the agent term in a locale, or neither."""
    for address, group in _groups(resolution.resulting).items():
        base = _group_path(address)

        def blame(locale: str, key: str, base: str = base) -> str:
            return path_of(base, "texts", (locale, key))

        yield from term_pair_refusals(group.children.get("texts", ()), blame)


INTEGRITY: tuple[IntegrityRule, ...] = (
    _slug_rules,
    _open_day_rules,
    _grid_and_archive_rules,
    _archived_rules,
    _capacity_rules,
    _term_rules,
)
"""The rules across objects that concern groups."""

__all__ = ["GROUP", "GROUP_TEXT", "INTEGRITY", "check_form", "check_reminders", "schedule_of"]
