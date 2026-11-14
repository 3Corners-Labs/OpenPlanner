"""The group in a version-1 configuration document (ADR 0099): kind, rules and integrity."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.adapters.wxcc import DESCRIPTOR as WEBEX_CC
from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import Change, ChangeAction, RuleContext, SlotLoad, Usage
from app.domain.configuration_document import to_json
from app.domain.configuration_plan import Plan
from app.domain.configuration_schema import (
    Anchor,
    Collection,
    Format,
    Kind,
    Shape,
    strings,
    text,
)
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_calendars import INTEGRITY as CALENDAR_INTEGRITY
from app.domain.configuration_v1_group import (
    GROUP,
    GROUP_TEXT,
    INTEGRITY,
    check_form,
    check_reminders,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing_codec import routing_from_json, routing_to_json
from app.domain.scheduling import PhoneMode
from tests.support.configuration import Recorder, entity, parse, plan, refusals
from tests.support.configuration_columns import uncovered_columns

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CONTEXT = RuleContext(
    now=NOW,
    settings={"booking_form_max_custom_fields": 2},
    contact_centers={WEBEX_CC.kind.value: WEBEX_CC},
)
ADDRESS = ("acme", "team")

HOLIDAY = Kind(
    "holiday",
    key=("day",),
    permission=Permission.HOLIDAYS_MANAGE,
    fields=(text("day", required=True), text("name", required=True)),
)
CALENDAR = Kind(
    "holiday_calendar",
    key=("slug",),
    permission=Permission.HOLIDAYS_MANAGE,
    fields=(text("slug", required=True), text("name", default="Kalender")),
    collections=(Collection("days", HOLIDAY, Shape.MAP),),
)
PLATFORM = Kind(
    "platform",
    permission=Permission.SETTINGS_PLATFORM_MANAGE,
    deletable=False,
    collections=(Collection("holiday_calendars", CALENDAR, Shape.ENTITIES),),
)
ORGANISATION = Kind(
    "organisation",
    key=("slug",),
    permission=Permission.ORGANISATION_MANAGE,
    anchor=Anchor.ORGANISATION,
    fields=(
        text("slug", required=True),
        text("name", default="Acme"),
        strings("holiday_calendars"),
    ),
    collections=(Collection("groups", GROUP, Shape.ENTITIES),),
)
ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", PLATFORM, Shape.SINGLE),
        Collection("organisations", ORGANISATION, Shape.ENTITIES),
    ),
)
FORMAT = Format("test/v1", ROOT, integrity=(*INTEGRITY, *CALENDAR_INTEGRITY))

RULE = {
    "weekday": 0,
    "opens": "09:00",
    "closes": "17:00",
    "capacity": 3,
    "label": None,
    "offer": "scheduled",
    "subjects": [],
    "valid_from": None,
    "valid_to": None,
}
FIELD = {
    "key": "customer_no",
    "type": "short_text",
    "labels": {"nl": "Klantnummer", "en": "Number"},
}
NEW_TEAM = {"slug": "team", "name": "Team", "timezone": "Europe/Amsterdam", "slot_minutes": 30}


def doc(*groups: dict[str, Any], organisation: str = "acme", **extra: Any) -> dict[str, Any]:
    """Return a document that states groups of one organisation."""
    return {"organisations": [{"slug": organisation, "groups": list(groups), **extra}]}


def planned(data: dict[str, Any], current: Entity | None = None, **options: Any) -> Plan:
    """Plan ``data`` against ``current`` in the test format with the test context."""
    options.setdefault("context", CONTEXT)
    return plan(data, current if current is not None else world(org("acme")), fmt=FORMAT, **options)


def group_values(slug: str = "team", **over: Any) -> dict[str, Any]:
    """Return every field of a group as the planner holds a created one, with ``over``."""
    result = planned(doc({**NEW_TEAM, "slug": slug}))
    values = dict(next(c for c in result.changes if c.kind == "group").values)
    values.update(over)
    return values


def team(slug: str = "team", children: Any = None, **over: Any) -> Entity:
    """Return an existing group."""
    return entity("group", (slug,), group_values(slug, **over), children or {})


def org(slug: str, *groups: Entity, calendars: tuple[str, ...] = ()) -> Entity:
    """Return an existing organisation holding ``groups``."""
    fields = {"name": "Acme", "holiday_calendars": list(calendars)}
    return entity("organisation", (slug,), fields, {"groups": tuple(groups)})


def calendar(slug: str, *days: str) -> Entity:
    """Return an existing holiday calendar with closed ``days``."""
    holidays = tuple(entity("holiday", (day,), {"name": "Feestdag"}) for day in days)
    return entity("holiday_calendar", (slug,), {"name": slug}, {"days": holidays})


def world(*organisations: Entity, calendars: tuple[Entity, ...] = ()) -> Entity:
    """Return the installation as it is now."""
    platform = entity("platform", (), {}, {"holiday_calendars": calendars})
    return entity(
        "installation", (), {}, {"platform": (platform,), "organisations": tuple(organisations)}
    )


def group_change(result: Plan) -> Change:
    """Return the one change of a group in a plan."""
    return next(change for change in result.changes if change.kind == "group")


# --- the call page chosen by a service (ADR 0123 step 6a) --------------------------------------

READY = RuleContext(
    now=NOW,
    settings={"booking_form_max_custom_fields": 2, "embedded_call_enabled": True},
    contact_centers={WEBEX_CC.kind.value: WEBEX_CC},
    guest_check_passed=True,
)
EMBEDDED_TEAM = {**NEW_TEAM, "join_client": "embedded", "embedded_call_webex_option": "always"}


def test_a_new_embedded_group_is_accepted_where_the_form_would_accept_it() -> None:
    result = planned(doc(EMBEDDED_TEAM), context=READY)

    assert result.ok
    values = group_change(result).values
    assert (values["join_client"], values["embedded_call_webex_option"]) == ("embedded", "always")


@pytest.mark.parametrize(
    "context",
    [
        CONTEXT,  # the switch is off here
        RuleContext(now=NOW, settings={"embedded_call_enabled": True}),  # no guest minted yet
        RuleContext(now=NOW, settings={"embedded_call_enabled": False}, guest_check_passed=True),
    ],
)
def test_embedded_is_refused_where_the_form_refuses_it_with_its_field(context: RuleContext) -> None:
    result = planned(doc(EMBEDDED_TEAM), context=context)

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "join_client")
    ]


def test_a_group_that_already_holds_embedded_is_not_asked_again_for_the_switch_or_the_check() -> (
    None
):
    held = team(join_client="embedded")

    result = planned(doc({**EMBEDDED_TEAM, "name": "Other"}), world(org("acme", held)))

    assert result.ok  # CONTEXT: switch off and no check; keeping it is no new choice


def test_the_option_may_be_empty_and_must_otherwise_be_one_of_three() -> None:
    assert planned(doc({**NEW_TEAM, "embedded_call_webex_option": None})).ok
    with pytest.raises(DomainError, match="embedded_call_webex_option"):
        planned(doc({**NEW_TEAM, "embedded_call_webex_option": "sometimes"}))


# --- settings ---------------------------------------------------------------------------


def test_a_new_group_gets_the_defaults_of_the_dataclass_and_asks_for_the_right_permissions() -> (
    None
):
    result = planned(doc(NEW_TEAM))

    change = group_change(result)
    assert result.ok
    assert (change.action, change.address, change.group, change.organisation) == (
        ChangeAction.CREATE,
        ADDRESS,
        "team",
        "acme",
    )
    assert change.permissions == {Permission.GROUP_CREATE}
    assert change.values["phone_mode"] == "optional"
    assert change.values["dispatch_kind"] == "contact_center"
    assert change.values["join_client"] == "browser"
    assert change.values["min_notice_minutes"] == 60
    assert change.values["max_bookings_per_email"] == 3
    assert change.values["archived"] is False
    assert change.values["holiday_source"] == "inherit"
    assert change.values["schedule_rules"] == []
    assert change.values["contacts"] is None


@pytest.mark.parametrize("missing", ["name", "timezone", "slot_minutes"])
def test_a_new_group_must_state_its_name_timezone_and_slot_length(missing: str) -> None:
    data = {k: v for k, v in NEW_TEAM.items() if k != missing}

    assert refusals(planned(doc(data))) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, missing)
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slot_minutes", 7),
        ("timezone", "Mars/Olympus"),
        ("min_notice_minutes", 3),
        ("participant_change_lead_minutes", 2),
        ("planner_change_min_lead_minutes", 2),
        ("video_host_email", "no-address"),
        ("calendar_invite_address", "no-address"),
        ("name", "x" * 5 + "\x07"),
    ],
)
def test_a_setting_the_portal_would_refuse_is_refused_with_its_field(
    field: str, value: object
) -> None:
    result = planned(doc({**NEW_TEAM, "dispatch_lead_minutes": 5, field: value}))

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, field)
    ]


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"kind": "webex_cc", "attributes": {"channel": ""}}, "contact_center.channel"),
        ({"kind": "webex_cc", "routing_value": "bad\x07value"}, "contact_center.routing_value"),
        ({"kind": "unknown_platform"}, "contact_center.kind"),
        ({"kind": "webex_cc", "attributes": {"nonsense": "x"}}, "contact_center.attributes"),
        ({"kind": "webex_cc", "extra": 1}, "contact_center"),
    ],
)
def test_a_contact_center_routing_the_platform_refuses_is_refused_with_the_part_that_is_wrong(
    value: object, field: str
) -> None:
    result = planned(doc({**NEW_TEAM, "dispatch_lead_minutes": 5, "contact_center": value}))

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, field)
    ]


def test_a_contact_center_routing_is_stored_as_written_and_an_empty_one_is_no_routing() -> None:
    routing = {"kind": "webex_cc", "routing_value": "team-42", "attributes": {"channel": "video"}}

    kept = planned(doc({**NEW_TEAM, "dispatch_lead_minutes": 5, "contact_center": routing}))
    empty = planned(
        doc({**NEW_TEAM, "dispatch_lead_minutes": 5, "contact_center": {"kind": "webex_cc"}})
    )

    assert refusals(kept) == []
    assert kept.changes[0].values["contact_center"] == routing
    assert empty.changes[0].values["contact_center"] is None


def test_a_value_outside_its_bounds_is_refused_when_the_document_is_read() -> None:
    with pytest.raises(DomainError) as error:
        planned(doc({**NEW_TEAM, "horizon_days": 400}))

    assert error.value.field == "organisations[acme].groups[team].horizon_days"


def test_the_slug_new_is_an_admin_path_and_never_a_group() -> None:
    result = planned(doc({**NEW_TEAM, "slug": "new"}))

    assert refusals(result) == [("organisations[acme].groups[new]", ErrorCode.SLUG_TAKEN, "slug")]


def test_an_update_names_only_what_changes_and_a_restated_group_changes_nothing() -> None:
    current = world(org("acme", team()))

    same = planned(doc({**NEW_TEAM, "phone_mode": "optional"}), current)
    renamed = planned(doc({"slug": "team", "name": "Helpdesk"}), current)

    assert same.changes == ()
    change = group_change(renamed)
    assert (change.fields, change.permissions) == (("name",), {Permission.GROUP_EDIT})


def test_a_setting_that_depends_on_another_is_checked_on_the_whole() -> None:
    current = world(org("acme", team(dispatch_lead_minutes=30, min_notice_minutes=60)))

    result = planned(doc({"slug": "team", "min_notice_minutes": 10}), current)

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "min_notice_minutes")
    ]


def test_the_idp_value_is_a_shape_the_provider_can_send_and_needs_its_own_permission() -> None:
    bad = planned(doc({**NEW_TEAM, "idp_key": "Not Valid"}))
    good = planned(doc({"slug": "team", "idp_key": "team-a"}), world(org("acme", team())))

    assert refusals(bad) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "idp_key")
    ]
    assert group_change(good).permissions == {Permission.IDP_MAPPING_MANAGE}


def test_each_field_asks_for_the_permission_of_its_own_screen() -> None:
    current = world(org("acme", team()))
    data = doc(
        {
            "slug": "team",
            "schedule_rules": [RULE],
            "reminder_rules": [{"channel": "email", "minutes_before": 60}],
            "form_fields": [FIELD],
            "contacts": {"options": [{"kind": "phone", "value": "030 123 4567"}]},
            "holiday_source": "own",
            "archived": False,
            "name": "Helpdesk",
        }
    )

    result = planned(data, current)

    assert group_change(result).permissions == {
        Permission.SCHEDULE_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.GROUP_EDIT,
        Permission.CLOSURE_MANAGE,
    }


def test_the_targets_follow_the_level_of_each_permission() -> None:
    recorder = Recorder()
    current = world(org("acme", team()))

    planned(
        doc({"slug": "team", "schedule_rules": [RULE], "archived": True}),
        current,
        may_change=recorder,
    )
    planned(doc({**NEW_TEAM, "slug": "other"}), current, may_change=recorder)

    asked = set(recorder.asked)
    assert (Permission.SCHEDULE_MANAGE, Target.for_group("team", "acme")) in asked
    assert (Permission.GROUP_DELETE, Target.for_organisation("acme")) in asked
    assert (Permission.GROUP_CREATE, Target.for_organisation("acme")) in asked


# --- staffing -----------------------------------------------------------------------------


def test_a_staffing_rule_is_written_the_way_the_portal_stores_it() -> None:
    result = planned(doc({**NEW_TEAM, "schedule_rules": [{**RULE, "opens": "09:00:00"}]}))

    assert group_change(result).values["schedule_rules"] == [RULE]


def test_rules_and_exceptions_the_portal_refuses_are_refused() -> None:
    clash = [RULE, {**RULE, "opens": "16:00", "closes": "18:00"}]
    cases = {
        "schedule_rules": ({"schedule_rules": clash}, "schedule_rules"),
        "opens": ({"schedule_rules": [{**RULE, "opens": "nine"}]}, "opens"),
        "window": ({"schedule_rules": [{**RULE, "opens": "18:00"}]}, "window"),
        "valid_to": (
            {"schedule_rules": [{**RULE, "valid_from": "2026-12-01", "valid_to": "2026-11-01"}]},
            "valid_to",
        ),
        "valid_from": ({"schedule_rules": [{**RULE, "valid_from": "someday"}]}, "valid_from"),
        "closed_and_open": (
            {
                "schedule_overrides": [
                    {"day": "2026-12-24", "opens": None, "closes": None, "capacity": None},
                    {"day": "2026-12-24", "opens": "09:00", "closes": "12:00", "capacity": 1},
                ]
            },
            "schedule_overrides",
        ),
        "partial": (
            {"schedule_overrides": [{"day": "2026-12-24", "opens": "09:00"}]},
            "window",
        ),
        "day": ({"schedule_overrides": [{"day": "24-12-2026"}]}, "day"),
    }

    for name, (fields, field) in cases.items():
        result = planned(doc({**NEW_TEAM, **fields}))
        assert refusals(result) == [
            ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, field)
        ], name


def test_an_exception_for_a_day_that_has_passed_is_refused_only_when_it_is_new() -> None:
    old = {"day": "2026-11-01", "opens": None, "closes": None, "capacity": None}
    current = world(
        org(
            "acme",
            team(schedule_overrides=[{**old, "label": None, "offer": "scheduled", "subjects": []}]),
        )
    )

    kept = planned(doc({"slug": "team", "schedule_overrides": [old], "name": "Helpdesk"}), current)
    added = planned(
        doc({"slug": "team", "schedule_overrides": [old, {**old, "day": "2026-11-02"}]}), current
    )

    assert kept.ok
    assert refusals(added) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "day")
    ]


def test_the_today_of_the_exception_is_the_groups_own_today() -> None:
    # 2026-11-08 12:00 UTC is 2026-11-08 in Amsterdam and in Auckland already the 9th.
    auckland = {**NEW_TEAM, "timezone": "Pacific/Auckland"}
    override = {"day": "2026-11-08", "opens": None, "closes": None, "capacity": None}

    amsterdam = planned(doc({**NEW_TEAM, "schedule_overrides": [override]}))
    far_east = planned(doc({**auckland, "schedule_overrides": [override]}))

    assert amsterdam.ok
    assert refusals(far_east)[0][2] == "day"


# --- reminders ----------------------------------------------------------------------------


def test_reminders_are_normalised_and_limited_as_the_portal_does() -> None:
    reminder = {"channel": "email", "minutes_before": 60}

    ok = planned(doc({**NEW_TEAM, "reminder_rules": [reminder]}))
    twice = planned(doc({**NEW_TEAM, "reminder_rules": [reminder, reminder]}))
    many = planned(
        doc(
            {
                **NEW_TEAM,
                "reminder_rules": [
                    {"channel": "email", "minutes_before": 10 * n} for n in range(1, 7)
                ],
            }
        )
    )

    assert group_change(ok).values["reminder_rules"] == [{**reminder, "enabled": True}]
    assert refusals(twice)[0][1:] == (ErrorCode.REMINDER_EXISTS, "minutes_before")
    assert refusals(many)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "reminders")


def test_a_reminder_offset_outside_the_bounds_is_refused() -> None:
    with pytest.raises(DomainError):
        planned(doc({**NEW_TEAM, "reminder_rules": [{"channel": "email", "minutes_before": 1}]}))
    with pytest.raises(DomainError):
        planned(
            doc({**NEW_TEAM, "reminder_rules": [{"channel": "whatsapp", "minutes_before": 60}]})
        )


def test_sms_needs_a_phone_field_but_only_a_new_rule_is_checked() -> None:
    sms = {"channel": "sms", "minutes_before": 60, "enabled": True}
    hidden = {**NEW_TEAM, "phone_mode": "hidden"}
    current = world(org("acme", team(phone_mode="optional", reminder_rules=[sms])))

    refused = planned(doc({**hidden, "reminder_rules": [sms]}))
    kept = planned(doc({"slug": "team", "phone_mode": "hidden"}), current)

    assert refusals(refused) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "channel")
    ]
    assert kept.ok


def test_the_reminder_check_can_be_called_on_its_own() -> None:
    items: list[dict[str, Any]] = [{"channel": "sms", "minutes_before": 15, "enabled": False}]

    assert check_reminders(items, items, PhoneMode.HIDDEN) == items
    with pytest.raises(DomainError):
        check_reminders(items, [], PhoneMode.HIDDEN)


# --- booking form -------------------------------------------------------------------------


def test_a_form_field_is_checked_by_the_form_screens_own_rules_and_written_in_full() -> None:
    result = planned(doc({**NEW_TEAM, "form_fields": [FIELD]}))

    (written,) = group_change(result).values["form_fields"]
    assert written == {
        "key": "customer_no",
        "type": "short_text",
        "required": False,
        "classification": "pii",
        "options": [],
        "minimum": None,
        "maximum": None,
        "labels": {"nl": "Klantnummer", "en": "Number"},
        "hints": {},
        "option_labels": {},
        "retired": False,
    }


@pytest.mark.parametrize(
    ("item", "code", "field"),
    [
        ({**FIELD, "key": "BSN"}, ErrorCode.INVALID_FORM_FIELD, "key"),
        ({**FIELD, "key": "bsn_number"}, ErrorCode.FORM_FIELD_FORBIDDEN, "key"),
        (
            {**FIELD, "type": "email", "classification": "not_pii"},
            ErrorCode.INVALID_FORM_FIELD,
            "classification",
        ),
        ({**FIELD, "type": "choice"}, ErrorCode.INVALID_FORM_FIELD, "options"),
        ({**FIELD, "labels": {"nl": "Alleen Nederlands"}}, ErrorCode.INVALID_FORM_FIELD, "label"),
        ({**FIELD, "labels": {"nl": 5}}, ErrorCode.INVALID_FORM_FIELD, "label"),
        ({**FIELD, "minimum": 5}, ErrorCode.INVALID_FORM_FIELD, "minimum"),
    ],
)
def test_a_form_field_the_portal_would_refuse_is_refused(
    item: dict[str, Any], code: ErrorCode, field: str
) -> None:
    result = planned(doc({**NEW_TEAM, "form_fields": [item]}))

    assert refusals(result) == [("organisations[acme].groups[team]", code, field)]


def test_the_form_is_limited_in_active_fields_and_keys_are_unique() -> None:
    many = [{**FIELD, "key": f"field_{n}"} for n in range(3)]

    over = planned(doc({**NEW_TEAM, "form_fields": many}))
    twice = planned(doc({**NEW_TEAM, "form_fields": [FIELD, FIELD]}))
    retired = planned(doc({**NEW_TEAM, "form_fields": [*many[:2], {**many[2], "retired": True}]}))

    assert refusals(over)[0][1:] == (ErrorCode.INVALID_FORM_FIELD, "fields")
    assert refusals(twice)[0][1:] == (ErrorCode.INVALID_FORM_FIELD, "key")
    assert retired.ok
    assert [f["retired"] for f in group_change(retired).values["form_fields"]] == [
        False,
        False,
        True,
    ]


def test_a_form_that_is_over_a_lowered_limit_stays_editable_when_it_adds_no_field() -> None:
    three = [{**FIELD, "key": f"field_{n}"} for n in range(3)]
    stored = [check_form(three, [], 5)][0]
    current = world(org("acme", team(form_fields=stored)))

    edited = planned(
        doc({"slug": "team", "form_fields": [{**three[0], "required": True}, *three[1:]]}),
        current,
    )
    added = planned(
        doc({"slug": "team", "form_fields": [*three, {**FIELD, "key": "extra_field"}]}), current
    )

    assert edited.ok
    assert refusals(added)[0][1:] == (ErrorCode.INVALID_FORM_FIELD, "fields")


def test_an_existing_field_keeps_its_type_a_retired_one_stays_retired_and_a_missing_one_goes() -> (
    None
):
    stored = check_form(
        [FIELD, {**FIELD, "key": "old_field", "retired": True}],
        [],
        5,
    )
    current = world(org("acme", team(form_fields=stored)))

    retyped = planned(
        doc({"slug": "team", "form_fields": [{**FIELD, "type": "long_text"}]}), current
    )
    revived = planned(
        doc({"slug": "team", "form_fields": [FIELD, {**FIELD, "key": "old_field"}]}), current
    )
    dropped = planned(doc({"slug": "team", "form_fields": []}), current)
    kept = planned(
        doc({"slug": "team", "form_fields": [stored[1], FIELD]}),
        current,
    )

    assert refusals(retyped)[0][1:] == (ErrorCode.INVALID_FORM_FIELD, "type")
    assert refusals(revived)[0][1:] == (ErrorCode.INVALID_FORM_FIELD, "key")
    assert dropped.ok
    assert kept.changes == ()


def test_the_form_check_can_be_called_on_its_own() -> None:
    assert check_form([], [], 0) == []
    with pytest.raises(DomainError):
        check_form([{**FIELD, "retired": False, "options": [], "minimum": None}], [], 0)


# --- contacts and texts -------------------------------------------------------------------


def test_a_contact_block_is_normalised_checked_and_empty_means_none() -> None:
    block = {"options": [{"kind": "phone", "value": "  030 123 4567 "}]}

    ok = planned(doc({**NEW_TEAM, "contacts": block}))
    empty = planned(doc({**NEW_TEAM, "contacts": {"options": []}}))
    bad = planned(
        doc({**NEW_TEAM, "contacts": {"options": [{"kind": "website", "value": "http://x"}]}})
    )

    assert group_change(ok).values["contacts"]["options"][0]["value"] == "030 123 4567"  # type: ignore[index]
    assert group_change(empty).values["contacts"] is None
    assert refusals(bad) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "contact_1_url")
    ]


def test_a_text_follows_the_rules_of_the_message_blocks() -> None:
    current = world(org("acme", team()))

    def texts(**blocks: dict[str, str]) -> dict[str, Any]:
        return doc({"slug": "team", "texts": blocks})

    ok = planned(texts(nl={"message.signoff": "  Tot ziens, {group_name} "}), current)
    placeholder = planned(texts(nl={"message.signoff": "Hallo {nobody}"}), current)
    unknown_key = planned(texts(nl={"no.such.block": "x"}), current)
    unknown_locale = planned(texts(fr={"message.signoff": "x"}), current)

    (change,) = ok.changes
    assert change.kind == "group_text"
    assert change.values["text"] == "Tot ziens, {group_name}"
    assert change.permissions == {Permission.TEXTS_MANAGE}
    assert refusals(placeholder)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "message.signoff")
    assert refusals(unknown_key)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "key")
    assert refusals(unknown_locale)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "locale")


def test_the_agent_term_is_set_in_both_words_or_in_neither() -> None:
    current = world(org("acme", team()))

    def texts(**words: str) -> dict[str, Any]:
        return doc({"slug": "team", "texts": {"nl": words}})

    one = planned(texts(**{"term.agent": "behandelaar"}), current)
    both = planned(texts(**{"term.agent": "behandelaar", "term.agents": "behandelaars"}), current)
    short = planned(texts(**{"term.agent": "b", "term.agents": "behandelaars"}), current)

    assert refusals(one) == [
        (
            "organisations[acme].groups[team].texts[nl][term.agents]",
            ErrorCode.INVALID_CONFIGURATION,
            "term.agents",
        )
    ]
    assert both.ok
    assert refusals(short)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "term.agent")


# --- rules across objects -----------------------------------------------------------------


def test_a_slug_is_unique_across_organisations_and_an_idp_value_within_one() -> None:
    current = world(org("acme", team("team")), org("zeta"))

    slug = planned(doc({**NEW_TEAM}, organisation="zeta"), current)
    idp = planned(doc({**NEW_TEAM, "slug": "other", "idp_key": "team"}), current)
    free = planned(doc({**NEW_TEAM, "slug": "other"}), current)

    assert refusals(slug) == [("organisations[zeta].groups[team]", ErrorCode.SLUG_TAKEN, "slug")]
    assert refusals(idp) == [
        ("organisations[acme].groups[other]", ErrorCode.IDP_KEY_TAKEN, "idp_key")
    ]
    assert free.ok


def test_the_calendars_a_group_names_must_exist() -> None:
    current = world(org("acme", team()), calendars=(calendar("nl"),))

    unknown = planned(doc({"slug": "team", "holiday_calendars": ["be"]}), current)
    known = planned(doc({"slug": "team", "holiday_calendars": ["nl"]}), current)

    assert refusals(unknown) == [
        (
            "organisations[acme].groups[team]",
            ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND,
            "holiday_calendars",
        )
    ]
    assert known.ok


def test_a_new_open_day_must_be_a_holiday_of_the_groups_calendars_and_not_in_the_past() -> None:
    days = ("2026-12-25", "2026-11-01")
    current = world(
        org("acme", team(), calendars=("nl",)),
        calendars=(calendar("nl", *days),),
    )

    ok = planned(doc({"slug": "team", "holiday_open_days": ["2026-12-25"]}), current)
    not_a_holiday = planned(doc({"slug": "team", "holiday_open_days": ["2026-12-26"]}), current)
    past = planned(doc({"slug": "team", "holiday_open_days": ["2026-11-01"]}), current)
    own = planned(
        doc({"slug": "team", "holiday_source": "own", "holiday_open_days": ["2026-12-25"]}), current
    )

    assert ok.ok
    assert refusals(not_a_holiday)[0][1:] == (ErrorCode.HOLIDAY_NOT_FOUND, "holiday_open_days")
    assert refusals(past)[0][1:] == (ErrorCode.HOLIDAY_NOT_FOUND, "holiday_open_days")
    assert refusals(own)[0][1:] == (ErrorCode.HOLIDAY_NOT_FOUND, "holiday_open_days")


def test_an_open_day_that_was_already_there_is_not_checked_again() -> None:
    current = world(org("acme", team(holiday_open_days=["2026-10-01"])))

    result = planned(
        doc({"slug": "team", "holiday_open_days": ["2026-10-01"], "name": "Helpdesk"}), current
    )

    assert result.ok


def test_a_bad_open_day_text_is_refused() -> None:
    result = planned(doc({**NEW_TEAM, "holiday_open_days": ["Kerst"]}))

    assert refusals(result)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "holiday_open_days")


def test_the_time_grid_is_frozen_while_bookings_are_ahead() -> None:
    current = world(org("acme", team()))
    usage = Usage(future_bookings=frozenset({ADDRESS}))

    slot = planned(doc({"slug": "team", "slot_minutes": 15}), current, usage=usage)
    zone = planned(doc({"slug": "team", "timezone": "Europe/Berlin"}), current, usage=usage)
    other = planned(doc({"slug": "team", "name": "Helpdesk"}), current, usage=usage)
    quiet = planned(doc({"slug": "team", "slot_minutes": 15}), current)

    assert refusals(slot) == [
        ("organisations[acme].groups[team]", ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, "slot_minutes")
    ]
    assert refusals(zone)[0][1:] == (ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, "timezone")
    assert other.ok
    assert quiet.ok


def test_archiving_needs_no_bookings_ahead_and_cannot_be_undone() -> None:
    current = world(org("acme", team(), team("old", archived=True)))
    busy = Usage(future_bookings=frozenset({ADDRESS}))

    archived = planned(doc({"slug": "team", "archived": True}), current)
    blocked = planned(doc({"slug": "team", "archived": True}), current, usage=busy)
    undone = planned(doc({"slug": "old", "archived": False}), current)
    born_archived = planned(doc({**NEW_TEAM, "slug": "gone", "archived": True}), current)

    assert group_change(archived).permissions == {Permission.GROUP_DELETE}
    assert refusals(blocked)[0][1:] == (ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, "archived")
    assert refusals(undone)[0][1:] == (ErrorCode.GROUP_ARCHIVED, "archived")
    assert born_archived.ok


def test_an_archived_group_and_what_is_in_it_is_read_only_but_can_be_deleted() -> None:
    current = world(org("acme", team("old", archived=True)))

    rename = planned(doc({"slug": "old", "name": "Else"}), current)
    text_ = planned(
        doc({"slug": "old", "texts": {"nl": {"message.signoff": "Tot ziens"}}}), current
    )
    unchanged = planned(doc({"slug": "old", "archived": True}), current)
    gone = planned(doc({"slug": "old", "action": "delete"}), current)

    assert refusals(rename) == [("organisations[acme].groups[old]", ErrorCode.GROUP_ARCHIVED, None)]
    assert refusals(text_)[0][1] is ErrorCode.GROUP_ARCHIVED
    assert unchanged.ok
    assert gone.ok


def slots(booked: int) -> Usage:
    """A Monday 10:00 (Amsterdam) slot with ``booked`` places taken, next week."""
    start = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    end = datetime(2026, 11, 9, 9, 30, tzinfo=UTC)
    return Usage(slot_loads={ADDRESS: (SlotLoad(start, end, booked),)})


def test_the_staffing_may_not_drop_below_what_a_slot_has_booked() -> None:
    current = world(org("acme", team(schedule_rules=[RULE])))

    lower = planned(
        doc({"slug": "team", "schedule_rules": [{**RULE, "capacity": 1}]}), current, usage=slots(2)
    )
    enough = planned(
        doc({"slug": "team", "schedule_rules": [{**RULE, "capacity": 2}]}), current, usage=slots(2)
    )
    removed = planned(doc({"slug": "team", "schedule_rules": []}), current, usage=slots(1))
    closed = planned(
        doc(
            {
                "slug": "team",
                "schedule_overrides": [
                    {"day": "2026-11-09", "opens": None, "closes": None, "capacity": None}
                ],
            }
        ),
        current,
        usage=slots(1),
    )
    nobody = planned(
        doc({"slug": "team", "schedule_rules": [{**RULE, "capacity": 1}]}), current, usage=slots(0)
    )

    assert refusals(lower) == [
        (
            "organisations[acme].groups[team]",
            ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED,
            "schedule_rules",
        )
    ]
    assert enough.ok
    assert refusals(removed)[0][1] is ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED
    assert refusals(closed)[0][1] is ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED
    assert nobody.ok


def test_a_shortfall_that_was_already_there_is_not_made_worse_by_an_unrelated_change() -> None:
    current = world(org("acme", team(schedule_rules=[{**RULE, "capacity": 1}])))

    same = planned(
        doc({"slug": "team", "schedule_rules": [{**RULE, "capacity": 1, "label": "Zelfde"}]}),
        current,
        usage=slots(3),
    )
    raised = planned(
        doc({"slug": "team", "schedule_rules": [{**RULE, "capacity": 2}]}), current, usage=slots(3)
    )

    assert same.ok
    assert raised.ok


def test_a_holiday_calendar_that_closes_the_slots_day_counts_as_a_cut_in_staffing() -> None:
    current = world(
        org("acme", team(schedule_rules=[RULE])),
        calendars=(calendar("nl", "2026-11-09"),),
    )
    own = {"slug": "team", "holiday_source": "own", "holiday_calendars": ["nl"]}

    closed = planned(doc(own), current, usage=slots(1))
    kept_open = planned(doc({**own, "holiday_open_days": ["2026-11-09"]}), current, usage=slots(1))
    nobody_booked = planned(doc(own), current, usage=slots(0))

    assert refusals(closed)[0][1:] == (
        ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED,
        "schedule_rules",
    )
    assert kept_open.ok
    assert nobody_booked.ok


def holiday_on(day: str) -> dict[str, Any]:
    """A platform document that gives calendar ``nl`` the holiday ``day``."""
    return {"platform": {"holiday_calendars": [{"slug": "nl", "days": {day: "Feestdag"}}]}}


def test_a_holiday_added_to_a_calendar_cuts_the_staffing_of_a_group_the_plan_does_not_touch() -> (
    None
):
    own = team(schedule_rules=[RULE], holiday_source="own", holiday_calendars=["nl"])
    current = world(org("acme", own), calendars=(calendar("nl"),))

    closed = planned(holiday_on("2026-11-09"), current, usage=slots(1))
    elsewhere = planned(holiday_on("2026-11-10"), current, usage=slots(1))
    nobody_booked = planned(holiday_on("2026-11-09"), current, usage=slots(0))

    assert refusals(closed) == [
        (
            "organisations[acme].groups[team]",
            ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED,
            "schedule_rules",
        )
    ]
    assert elsewhere.ok
    assert nobody_booked.ok


def test_choosing_a_calendar_for_the_organisation_cuts_the_staffing_of_its_groups() -> None:
    current = world(
        org("acme", team(schedule_rules=[RULE])), calendars=(calendar("nl", "2026-11-09"),)
    )

    chosen = planned(
        {"organisations": [{"slug": "acme", "holiday_calendars": ["nl"]}]}, current, usage=slots(1)
    )

    assert refusals(chosen)[0][1:] == (
        ErrorCode.CONFIGURATION_CAPACITY_BELOW_BOOKED,
        "schedule_rules",
    )


def test_a_group_that_cannot_be_built_is_a_refusal_not_a_skipped_check() -> None:
    broken = team(schedule_rules=[RULE], slot_minutes=7)
    current = world(org("acme", broken), calendars=(calendar("nl"),))

    result = planned(holiday_on("2026-11-09"), current, usage=slots(1))

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "slot_minutes")
    ]


# --- structure ----------------------------------------------------------------------------


def test_a_group_document_survives_reading_and_writing() -> None:
    data = doc(
        {
            **NEW_TEAM,
            "min_notice_minutes": 90,
            "video_host_email": "host@voorbeeld.nl",
            "idp_key": "team-a",
            "holiday_source": "own",
            "holiday_calendars": ["nl"],
            "holiday_open_days": ["2026-12-25"],
            "archived": False,
            "form_fields": [FIELD],
            "schedule_rules": [RULE],
            "schedule_overrides": [
                {"day": "2026-12-24", "opens": None, "closes": None, "capacity": None}
            ],
            "reminder_rules": [{"channel": "email", "minutes_before": 60, "enabled": True}],
            "contacts": {"options": [{"kind": "phone", "value": "030 123 4567"}]},
            "texts": {"nl": {"message.signoff": "Tot ziens"}},
        },
        holiday_calendars=["nl"],
    )

    written = to_json(parse(data, FORMAT))
    group = written["organisations"][0]["groups"][0]  # type: ignore[index]

    assert group["texts"] == {"nl": {"message.signoff": "Tot ziens"}}
    assert group["schedule_rules"][0]["capacity"] == 3
    assert group["form_fields"][0]["labels"]["nl"] == "Klantnummer"
    assert group["slug"] == "team"


def test_every_column_of_the_group_tables_is_covered() -> None:
    assert uncovered_columns([GROUP, GROUP_TEXT]) == {}


def test_the_kinds_name_the_tables_the_assembler_expects() -> None:
    assert GROUP.name == "group"
    assert GROUP.key == ("slug",)
    assert GROUP.anchor is Anchor.GROUP
    assert [c.name for c in GROUP.collections] == ["texts"]
    assert GROUP_TEXT.key == ("locale", "key")
    assert GROUP.tables | GROUP_TEXT.tables == {
        "groups",
        "group_texts",
        "group_contact_center",
        "group_form_fields",
        "group_contacts",
        "group_holiday_calendars",
        "group_holiday_open_days",
        "schedule_rules",
        "schedule_overrides",
        "reminder_rules",
    }


# --- fallback, routing and subjects (ADR 0081, ADR 0082) ------------------------------------

ROUTING = {
    "subjects": [
        {"code": "billing", "labels": {"nl": "Factuur", "en": "Invoice"}},
        {"code": "other", "labels": {"nl": "Anders", "en": "Other"}},
    ],
    "attributes": ["preferred_language"],
    "mapping": {"subject": {"name": "topic", "values": {"billing": "B"}}},
}


def test_a_new_group_has_no_fallback_and_no_routing() -> None:
    values = group_change(planned(doc(NEW_TEAM))).values

    assert values["dispatch_fallback"] == "none"
    assert values["fallback_space_room_id"] is None
    assert values["routing"] is None


def test_the_fallback_is_checked_like_the_portal_checks_it() -> None:
    own = {
        **NEW_TEAM,
        "dispatch_fallback": "own_queue",
        "fallback_space_room_id": "Y2lzY29zcGFyazo",
    }
    bad_room = {**NEW_TEAM, "fallback_space_room_id": "no good"}

    ok = planned(doc(own))
    refused = planned(doc(bad_room))

    assert group_change(ok).values["dispatch_fallback"] == "own_queue"
    assert refusals(refused) == [
        (
            "organisations[acme].groups[team]",
            ErrorCode.INVALID_CONFIGURATION,
            "fallback_space_room_id",
        )
    ]
    with pytest.raises(DomainError):
        parse(doc({**NEW_TEAM, "dispatch_fallback": "elsewhere"}), FORMAT)


def test_routing_is_normalised_through_the_codec_and_an_empty_one_is_none() -> None:
    created = group_change(planned(doc({**NEW_TEAM, "routing": ROUTING}))).values["routing"]
    emptied = planned(
        doc({"slug": "team", "routing": {}}), world(org("acme", team(routing=ROUTING)))
    )

    assert created == routing_to_json(routing_from_json(ROUTING))
    assert group_change(emptied).values["routing"] is None


def test_routing_the_portal_would_refuse_is_refused() -> None:
    bad = {"subjects": [{"code": "Bad Code", "labels": {"nl": "A", "en": "A"}}]}

    result = planned(doc({**NEW_TEAM, "routing": bad}))

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "subject_code")
    ]


def test_a_block_offers_the_subjects_it_names_sorted_and_without_repeats() -> None:
    rule = {**RULE, "subjects": ["other", "billing", "other"]}

    result = planned(doc({**NEW_TEAM, "schedule_rules": [rule]}))

    assert group_change(result).values["schedule_rules"][0]["subjects"] == ["billing", "other"]


def test_a_subject_code_that_is_no_code_is_refused() -> None:
    result = planned(doc({**NEW_TEAM, "schedule_rules": [{**RULE, "subjects": ["Bad Code"]}]}))

    assert refusals(result) == [
        ("organisations[acme].groups[team]", ErrorCode.INVALID_CONFIGURATION, "subjects")
    ]
