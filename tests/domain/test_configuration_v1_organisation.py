"""The organisation of the version-1 format (ADR 0099): fields, children and rules."""

import base64
import hashlib
from collections.abc import Mapping
from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction, RuleContext, Usage
from app.domain.configuration_document import parse_document, to_json
from app.domain.configuration_plan import Plan
from app.domain.configuration_schema import Collection, Format, Kind, Shape, text
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_organisation import INTEGRITY, ORGANISATION
from app.domain.configuration_values import Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.text_overrides import EDITABLE_BY_KEY
from tests.fakes.woff2 import make_woff2
from tests.support.configuration import Recorder
from tests.support.configuration import plan as engine_plan
from tests.support.configuration_columns import uncovered_columns
from tests.support.images import png

CALENDAR = Kind(
    "holiday_calendar",
    key=("slug",),
    permission=Permission.HOLIDAYS_MANAGE,
    fields=(text("slug", required=True), text("name", default="")),
)
PLATFORM = Kind(
    "platform",
    permission=Permission.SETTINGS_PLATFORM_MANAGE,
    deletable=False,
    collections=(Collection("holiday_calendars", CALENDAR, Shape.ENTITIES),),
)
CLIENT = Kind(
    "api_client",
    key=("client_id",),
    permission=Permission.API_CLIENTS_MANAGE,
    organisation_field="organisation",
    fields=(text("client_id", required=True), text("organisation", required=True)),
)
ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", PLATFORM, Shape.SINGLE),
        Collection("organisations", ORGANISATION, Shape.ENTITIES),
        Collection("api_clients", CLIENT, Shape.ENTITIES),
    ),
)
FORMAT = Format("test/v1", ROOT, integrity=INTEGRITY)

DEFAULTS: dict[str, Value] = {
    "name": "Acme",
    "idp_key": None,
    "default_locale": "nl",
    "display_name": None,
    "accent_color": "#01689B",
    "header_style": "standard",
    "theme_tokens": {},
    "contacts": None,
    "origins": [],
    "holiday_calendars": [],
}


def org(
    slug: str = "acme", children: Mapping[str, tuple[Entity, ...]] | None = None, **f: Value
) -> Entity:
    return Entity("organisation", (slug,), fields={**DEFAULTS, **f}, children=dict(children or {}))


def installation(
    *orgs: Entity, calendars: tuple[str, ...] = (), clients: tuple[Entity, ...] = ()
) -> Entity:
    held = tuple(Entity("holiday_calendar", (slug,), fields={"name": slug}) for slug in calendars)
    platform = Entity("platform", children={"holiday_calendars": held})
    return Entity(
        "installation",
        children={"platform": (platform,), "organisations": orgs, "api_clients": clients},
    )


def plan(data: Mapping[str, Any], current: Entity | None = None, **kw: Any) -> Plan:
    return engine_plan(data, current or installation(org()), fmt=FORMAT, **kw)


def only(slug: str = "acme", **body: Any) -> dict[str, Any]:
    return {"organisations": [{"slug": slug, **body}]}


def refusals(result: Plan) -> list[tuple[str, ErrorCode, str | None]]:
    return [(r.path, r.code, r.field) for r in result.refusals]


def binary(data: bytes, media_type: str) -> dict[str, str]:
    return {
        "media_type": media_type,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode(),
    }


def test_a_new_organisation_gets_the_defaults_and_asks_for_the_rights_of_its_fields() -> None:
    result = plan(only("new-org", name="Nieuw", accent_color="#01689b", origins=["https://a.nl"]))

    (change,) = result.changes
    assert (change.action, change.address, change.fields) == (
        ChangeAction.CREATE,
        ("new-org",),
        ("name", "accent_color", "origins"),
    )
    assert change.values["accent_color"] == "#01689B"
    assert change.values["header_style"] == "standard"
    assert change.permissions == {
        Permission.ORGANISATION_MANAGE,
        Permission.BRANDING_MANAGE,
        Permission.EMBEDDING_MANAGE,
    }
    assert result.ok


def test_renaming_asks_for_the_organisation_right_only() -> None:
    (change,) = plan(only(name="Acme BV")).changes

    assert (change.fields, change.permissions) == (("name",), {Permission.ORGANISATION_MANAGE})


def test_each_screen_has_its_own_permission_and_target() -> None:
    recorder = Recorder()
    data = only(
        idp_key="acme-id",
        display_name="Acme",
        contacts={"options": [{"kind": "phone", "value": "030 123 4567"}]},
        holiday_calendars=["nationaal"],
    )

    plan(data, installation(org(), calendars=("nationaal",)), may_change=recorder)

    asked = set(recorder.asked)
    assert (Permission.IDP_MAPPING_MANAGE, Target.for_organisation("acme")) in asked
    assert (Permission.BRANDING_MANAGE, Target.for_organisation("acme")) in asked
    assert (Permission.ORGANISATION_CLOSURE, Target.for_organisation("acme")) in asked
    assert (Permission.HOLIDAYS_ASSIGN, Target.for_organisation("acme")) in asked


def test_deleting_an_organisation_asks_for_the_delete_right() -> None:
    (change,) = plan(only(action="delete")).changes

    assert (change.action, change.permissions) == (
        ChangeAction.DELETE,
        {Permission.ORGANISATION_DELETE},
    )


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"slug": "Bad Slug", "name": "X"}, ErrorCode.INVALID_CONFIGURATION, "slug"),
        ({"slug": "new", "name": "X"}, ErrorCode.SLUG_TAKEN, "slug"),
        ({"slug": "ok", "name": "x\x00y"}, ErrorCode.INVALID_CONFIGURATION, "name"),
        (
            {"slug": "ok", "name": "X", "default_locale": "de"},
            ErrorCode.INVALID_CONFIGURATION,
            "default_locale",
        ),
        (
            {"slug": "ok", "name": "X", "idp_key": "Not Valid"},
            ErrorCode.INVALID_CONFIGURATION,
            "idp_key",
        ),
        (
            {"slug": "ok", "name": "X", "accent_color": "#FFFF00"},
            ErrorCode.INVALID_CONFIGURATION,
            "accent_color",
        ),
        (
            {"slug": "ok", "name": "X", "accent_color": "blue"},
            ErrorCode.INVALID_CONFIGURATION,
            "accent_color",
        ),
        (
            {"slug": "ok", "name": "X", "theme_tokens": {"color-texxt": "#000000"}},
            ErrorCode.INVALID_CONFIGURATION,
            "color_texxt",
        ),
        (
            {"slug": "ok", "name": "X", "theme_tokens": {"color-text": 5}},
            ErrorCode.INVALID_CONFIGURATION,
            "color_text",
        ),
        (
            {"slug": "ok", "name": "X", "theme_tokens": {"color-text": "#F0F0F0"}},
            ErrorCode.INVALID_CONFIGURATION,
            "color_text",
        ),
        (
            {"slug": "ok", "name": "X", "contacts": {"options": [{"kind": "fax", "value": "1"}]}},
            ErrorCode.INVALID_CONFIGURATION,
            "contact_1_kind",
        ),
        (
            {"slug": "ok", "name": "X", "origins": ["http://example.com"]},
            ErrorCode.INVALID_CONFIGURATION,
            "origin_1",
        ),
        (
            {"slug": "ok", "name": "X", "origins": ["https://a.nl\nhttps://b.nl"]},
            ErrorCode.INVALID_CONFIGURATION,
            "origins",
        ),
        (
            {"slug": "ok", "name": "X", "holiday_calendars": ["Not A Slug"]},
            ErrorCode.INVALID_CONFIGURATION,
            "holiday_calendars",
        ),
    ],
)
def test_what_the_admin_forms_refuse_is_refused(
    body: dict[str, Any], code: ErrorCode, field: str
) -> None:
    result = plan({"organisations": [body]}, installation())

    assert refusals(result) == [(f"organisations[{body['slug']}]", code, field)]


def test_a_value_longer_than_its_field_is_refused_when_the_document_is_read() -> None:
    with pytest.raises(DomainError) as error:
        plan(only("ok", name="X", display_name="n" * 201))

    assert error.value.field == "organisations[ok].display_name"


def test_branding_is_normalised_and_the_theme_is_stored_sorted() -> None:
    data = only(
        display_name="  Acme  ",
        accent_color="#01689b",
        theme_tokens={"radius": "8px", "color-text": "#222222"},
    )

    (change,) = plan(data).changes

    assert change.values["display_name"] == "Acme"
    assert change.values["accent_color"] == "#01689B"
    assert change.values["theme_tokens"] == {"color-text": "#222222", "radius": "8px"}


def test_a_contrast_failure_is_checked_only_when_branding_changes() -> None:
    legacy = org(theme_tokens={"color-text": "#F0F0F0"})

    unchanged = plan(only(name="Acme BV"), installation(legacy))
    touched = plan(only(header_style="accent"), installation(legacy))

    assert unchanged.ok
    assert refusals(touched) == [
        ("organisations[acme]", ErrorCode.INVALID_CONFIGURATION, "color_text")
    ]


def test_an_empty_display_name_becomes_none_and_a_contact_block_is_normalised() -> None:
    data = only(
        display_name="   ",
        contacts={"options": [{"kind": "phone", "value": "  030 123 4567 "}]},
    )

    (change,) = plan(data, installation(org(display_name="Old"))).changes

    assert change.values["display_name"] is None
    assert change.values["contacts"]["options"][0]["value"] == "030 123 4567"  # type: ignore[index]


def test_a_contact_block_without_options_is_no_block() -> None:
    (change,) = plan(
        only(contacts={"options": []}),
        installation(
            org(
                contacts={
                    "options": [
                        {"kind": "phone", "value": "030 123 4567", "labels": {}, "hours": {}}
                    ],
                    "headings": {},
                }
            )
        ),
    ).changes

    assert change.values["contacts"] is None


def test_origins_are_normalised_in_order_and_follow_the_environment() -> None:
    data = only(origins=["https://B.nl:443", "https://a.nl", "https://b.nl"])

    (change,) = plan(data).changes
    local = plan(only(origins=["http://localhost:3000"]))
    production = plan(only(origins=["http://localhost:3000"]), context=RuleContext(production=True))
    small = plan(
        only(origins=["https://a.nl", "https://b.nl"]),
        context=RuleContext(settings={"embedding_max_origins": 1}),
    )

    assert change.values["origins"] == ["https://b.nl", "https://a.nl"]
    assert local.ok
    assert refusals(production)[0][2] == "origin_1"
    assert refusals(small) == [
        ("organisations[acme]", ErrorCode.INVALID_CONFIGURATION, "origins_count")
    ]


def test_origins_in_another_order_are_a_change() -> None:
    current = installation(org(origins=["https://a.nl", "https://b.nl"]))

    assert plan(only(origins=["https://b.nl", "https://a.nl"]), current).changes[0].fields == (
        "origins",
    )
    assert plan(only(origins=["https://a.nl", "https://b.nl"]), current).changes == ()


def test_the_idp_key_may_not_be_shared_and_the_slug_counts_as_one() -> None:
    current = installation(org("acme"), org("beta"))

    shared = plan(only("beta", idp_key="acme"), current)
    free = plan(only("beta", idp_key="beta-id"), current)
    swap = plan(
        {
            "organisations": [
                {"slug": "acme", "idp_key": "beta"},
                {"slug": "beta", "idp_key": "acme"},
            ]
        },
        current,
    )

    assert refusals(shared) == [("organisations[beta]", ErrorCode.IDP_KEY_TAKEN, "idp_key")]
    assert free.ok
    assert swap.ok


def test_two_new_organisations_cannot_pick_the_same_idp_key() -> None:
    data = {
        "organisations": [
            {"slug": "a", "name": "A", "idp_key": "x"},
            {"slug": "b", "name": "B", "idp_key": "x"},
        ]
    }

    assert [r.code for r in plan(data, installation()).refusals] == [ErrorCode.IDP_KEY_TAKEN] * 2


def test_an_organisation_with_an_api_client_left_is_not_deleted() -> None:
    client = Entity("api_client", ("bot",), fields={"organisation": "acme"})
    current = installation(org(), clients=(client,))

    refused = plan(only(action="delete"), current)
    both = plan(
        {**only(action="delete"), "api_clients": [{"client_id": "bot", "action": "delete"}]},
        current,
    )

    assert refusals(refused) == [
        ("organisations[acme]", ErrorCode.ORGANISATION_HAS_API_CLIENTS, None)
    ]
    assert both.ok


def test_holiday_calendars_must_exist_in_the_result_and_repeats_are_dropped() -> None:
    current = installation(org(), calendars=("nationaal",))

    ok = plan(only(holiday_calendars=["nationaal", "nationaal"]), current)
    missing = plan(only(holiday_calendars=["nationaal", "weg"]), current)
    created = plan(
        {
            "platform": {"holiday_calendars": [{"slug": "weg", "name": "Weg"}]},
            **only(holiday_calendars=["weg"]),
        },
        current,
    )

    assert ok.changes[0].values["holiday_calendars"] == ["nationaal"]
    assert refusals(missing) == [
        ("organisations[acme]", ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND, "holiday_calendars")
    ]
    assert created.ok


def test_a_class_d_setting_is_read_as_the_registry_types_it() -> None:
    data = only(settings={"staff_dormant_after_days": 30, "time_format": "12H"})

    result = plan(data)

    assert [(c.kind, c.address, c.values["value"]) for c in result.changes] == [
        ("organisation_setting", ("acme", "staff_dormant_after_days"), 30),
        ("organisation_setting", ("acme", "time_format"), "12h"),
    ]
    assert {p for c in result.changes for p in c.permissions} == {
        Permission.SETTINGS_ORGANISATION_MANAGE
    }


@pytest.mark.parametrize(
    ("key", "value", "code", "field"),
    [
        ("rate_limit_bookings", "5/hour", ErrorCode.SETTING_NOT_EDITABLE, "rate_limit_bookings"),
        ("no_such_setting", 1, ErrorCode.SETTING_NOT_EDITABLE, "key"),
        ("staff_dormant_after_days", 1, ErrorCode.SETTING_INVALID, "staff_dormant_after_days"),
        ("staff_dormant_after_days", "30", ErrorCode.SETTING_INVALID, "staff_dormant_after_days"),
    ],
)
def test_only_class_d_settings_within_their_bounds_are_accepted(
    key: str, value: Any, code: ErrorCode, field: str
) -> None:
    result = plan(only(settings={key: value}))

    assert refusals(result) == [(f"organisations[acme].settings[{key}]", code, field)]


def test_a_setting_set_to_null_is_removed() -> None:
    current = installation(
        org(
            children={
                "settings": (
                    Entity("organisation_setting", ("time_format",), fields={"value": "12h"}),
                )
            }
        )
    )

    (change,) = plan(only(settings={"time_format": None}), current).changes

    assert (change.action, change.address) == (ChangeAction.DELETE, ("acme", "time_format"))


def test_settings_that_break_a_combined_rule_are_refused_by_the_context() -> None:
    seen: list[Mapping[str, object]] = []

    def combination(values: Mapping[str, object]) -> str | None:
        seen.append(dict(values))
        return "planner_rebook_reminder_days" if values.get("planner_rebook_days") == 2 else None

    context = RuleContext(combination_problem=combination)

    bad = plan(only(settings={"planner_rebook_days": 2}), context=context)
    good = plan(only(settings={"planner_rebook_days": 3}), context=context)
    untouched = plan(only(name="Acme BV"), context=context)

    assert refusals(bad) == [
        (
            "organisations[acme].settings",
            ErrorCode.SETTING_COMBINATION,
            "planner_rebook_reminder_days",
        )
    ]
    assert good.ok
    assert untouched.ok
    assert seen == [{"planner_rebook_days": 2}, {"planner_rebook_days": 3}]


def test_texts_are_cleaned_and_checked_against_the_allow_list() -> None:
    key = "term.agent"
    data = only(texts={"nl": {"term.agent": "  behandelaar ", "term.agents": "behandelaars"}})

    result = plan(data)

    assert [c.values["text"] for c in result.changes] == ["behandelaar", "behandelaars"]
    assert result.changes[0].permissions == {Permission.TEXTS_ORGANISATION_MANAGE}
    assert key in {c.key[1] for c in result.changes}


@pytest.mark.parametrize(
    ("locale", "key", "text_value", "field"),
    [
        ("de", "term.agent", "x y", "locale"),
        ("nl", "not.a.key", "x", "key"),
        ("nl", "term.agent", "a{b", "term.agent"),
    ],
)
def test_a_text_that_breaks_a_rule_is_refused_by_field_name(
    locale: str, key: str, text_value: str, field: str
) -> None:
    result = plan(only(texts={locale: {key: text_value}}))

    assert [(r.code, r.field) for r in result.refusals][0] == (
        ErrorCode.INVALID_CONFIGURATION,
        field,
    )


def test_the_two_words_of_the_agent_term_come_together() -> None:
    both = {
        "term.agent": Entity("organisation_text", ("nl", "term.agent"), fields={"text": "a"}),
        "term.agents": Entity("organisation_text", ("nl", "term.agents"), fields={"text": "b"}),
    }
    current = installation(org(children={"texts": tuple(both.values())}))

    one = plan(only(texts={"nl": {"term.agent": "behandelaar"}}), installation(org()))
    dropped = plan(only(texts={"nl": {"term.agents": None}}), current)
    both_dropped = plan(only(texts={"nl": {"term.agent": None, "term.agents": None}}), current)

    assert refusals(one) == [
        (
            "organisations[acme].texts[nl][term.agent]",
            ErrorCode.INVALID_CONFIGURATION,
            "term.agents",
        )
    ]
    assert [r.field for r in dropped.refusals] == ["term.agents"]
    assert both_dropped.ok


def test_other_texts_are_not_held_to_the_pair_rule() -> None:
    key = next(k for k in sorted(EDITABLE_BY_KEY) if not k.startswith("term."))
    current = installation(
        org(
            children={
                "texts": (Entity("organisation_text", ("nl", "term.agent"), fields={"text": "a"}),)
            }
        )
    )

    result = plan(only(texts={"nl": {key: None}}), current)

    assert result.ok


def test_the_logo_takes_the_media_type_of_its_bytes() -> None:
    wrong = binary(png(metadata=False), "image/jpeg")

    (change,) = plan(only(logo={"data": wrong})).changes

    assert (change.kind, change.permissions) == ("organisation_logo", {Permission.BRANDING_MANAGE})
    assert change.values["data"].media_type == "image/png"  # type: ignore[union-attr]
    refused = refusals(plan(only(logo={"data": binary(b"not an image", "image/png")})))
    assert ("organisations[acme].logo", ErrorCode.INVALID_CONFIGURATION, "logo") in refused


def test_a_font_is_checked_by_structure_and_the_ceiling_from_the_context() -> None:
    good = binary(make_woff2(), "font/woff2")
    data = only(fonts=[{"role": "text", "data": good}])

    ok = plan(data)
    over = plan(data, context=RuleContext(settings={"max_font_unpacked_bytes": 1_000}))
    broken = plan(only(fonts=[{"role": "heading", "data": binary(b"wOF2junk", "font/woff2")}]))

    assert ok.ok
    assert ok.changes[0].values["data"].media_type == "font/woff2"  # type: ignore[union-attr]
    assert refusals(over) == [
        ("organisations[acme].fonts[text]", ErrorCode.INVALID_CONFIGURATION, "font")
    ]
    assert refusals(broken)[0][2] == "font"
    with pytest.raises(DomainError) as unknown_role:
        plan(only(fonts=[{"role": "italic", "data": good}]))
    assert unknown_role.value.field == "organisations[acme].fonts[0].role"


def test_a_document_round_trips_through_json() -> None:
    data = {
        "format": "test/v1",
        "organisations": [
            {
                "slug": "acme",
                "name": "Acme",
                "idp_key": "acme-id",
                "origins": ["https://a.nl"],
                "holiday_calendars": ["nationaal"],
                "settings": {"time_format": "12h"},
                "texts": {"nl": {"term.agent": "behandelaar", "term.agents": None}},
                "logo": {"data": binary(png(metadata=False), "image/png")},
                "fonts": [{"role": "text", "data": binary(make_woff2(), "font/woff2")}],
            }
        ],
    }

    assert to_json(parse_document(data, {FORMAT.name: FORMAT})) == data


def test_every_column_of_the_tables_is_covered() -> None:
    kinds = [ORGANISATION, *(c.kind for c in ORGANISATION.collections)]

    assert uncovered_columns(kinds) == {}


def test_the_usage_facts_of_the_engine_still_guard_a_delete() -> None:
    usage = Usage(in_use={"organisation": frozenset({("acme",)})})

    assert (
        refusals(plan(only(action="delete"), usage=usage))[0][1]
        is ErrorCode.CONFIGURATION_OBJECT_IN_USE
    )
