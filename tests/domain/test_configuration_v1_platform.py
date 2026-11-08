"""The platform aggregate of the version-1 document (ADR 0099): settings, secrets, API, logs."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from app.domain.authorization import MATRIX, Permission, Role, Target
from app.domain.configuration_changes import ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_schema import Anchor, Collection, Format, Kind, Shape, strings, text
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_platform import (
    HOLIDAY_CALENDAR,
    INTEGRITY,
    LOG_DESTINATION,
    PLATFORM,
    PLATFORM_SECRET,
    PLATFORM_SETTING,
    ROLE,
    SECRET_NAMES,
    editable_setting,
)
from app.domain.configuration_values import Sealed, Secret, Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.settings_registry import BY_KEY, Topic
from tests.support.configuration import Recorder, allow, entity, refusals
from tests.support.configuration_columns import uncovered_columns

DOMAIN = Kind(
    "identity_provider_domain",
    key=("domain",),
    permission=Permission.IDENTITY_PROVIDERS_MANAGE,
    fields=(text("domain", required=True),),
)
PROVIDER = Kind(
    "identity_provider",
    key=("key",),
    permission=Permission.IDENTITY_PROVIDERS_MANAGE,
    fields=(text("key", required=True),),
    collections=(Collection("domains", DOMAIN, Shape.KEYS),),
)
GROUP = Kind(
    "group",
    key=("slug",),
    permission=Permission.GROUP_EDIT,
    anchor=Anchor.GROUP,
    fields=(text("slug", required=True), strings("holiday_calendars")),
)
ORGANISATION = Kind(
    "organisation",
    key=("slug",),
    permission=Permission.ORGANISATION_MANAGE,
    anchor=Anchor.ORGANISATION,
    fields=(text("slug", required=True), strings("holiday_calendars")),
    collections=(
        Collection("groups", GROUP, Shape.ENTITIES),
        Collection("identity_providers", PROVIDER, Shape.ENTITIES),
    ),
)
ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", PLATFORM, Shape.SINGLE),
        Collection("organisations", ORGANISATION, Shape.ENTITIES),
    ),
)
FORMAT = Format("test/v1", ROOT, integrity=INTEGRITY)
RATE = "rate_limit_bookings"
BATCH = "log_ship_batch_size"
SUFFIX = "bundled_idp_login_suffix"
LOGIN_KEY = next(key for key, spec in BY_KEY.items() if spec.topic is Topic.LOGIN_MAPPING)


def parse(data: Mapping[str, Any]) -> Document:
    return parse_document({"format": "test/v1", **data}, {FORMAT.name: FORMAT})


def platform(**collections: tuple[Entity, ...]) -> Entity:
    return entity("platform", (), {}, collections)


def current(
    platform_entity: Entity | None = None, organisations: tuple[Entity, ...] = ()
) -> Entity:
    children = {
        "platform": (platform_entity or platform(),),
        "organisations": organisations,
    }
    return entity("installation", (), {}, children)


def plan(
    data: Mapping[str, Any],
    state: Entity | None = None,
    *,
    usage: Usage | None = None,
    context: RuleContext | None = None,
    may_change: Any = allow,
    document: Document | None = None,
) -> Plan:
    return plan_document(
        document or parse(data),
        state or current(),
        usage=usage or Usage(),
        may_change=may_change,
        context=context or RuleContext(),
        settings_version=1,
    )


def with_secret(document: Document, collection: str, key: str, value: Value) -> Document:
    """Return the document with an entity's secret opened, as the adapter does before planning."""
    (platform_doc,) = document.root.children["platform"]
    entries = tuple(
        replace(e, fields={**e.fields, "secret": value}) if e.key == (key,) else e
        for e in platform_doc.children[collection]
    )
    opened = replace(platform_doc, children={**platform_doc.children, collection: entries})
    return replace(document, root=replace(document.root, children={"platform": (opened,)}))


# --- settings ----------------------------------------------------------------------------


def test_a_setting_is_created_with_its_value_as_the_registry_stores_it() -> None:
    result = plan({"platform": {"settings": {RATE: "5 / hour", BATCH: 50}}})

    assert [(c.kind, c.key, c.action) for c in result.changes] == [
        ("platform_setting", (RATE,), ChangeAction.CREATE),
        ("platform_setting", (BATCH,), ChangeAction.CREATE),
    ]
    assert result.changes[0].values["value"] == "5/hour"
    assert result.changes[0].fields == ("value",)
    assert result.changes[0].permissions == {Permission.SETTINGS_PLATFORM_MANAGE}
    assert result.ok


def test_a_setting_that_is_the_same_is_no_change_and_null_resets_it() -> None:
    state = current(platform(settings=(entity("platform_setting", (BATCH,), {"value": 50}),)))

    same = plan({"platform": {"settings": {BATCH: 50}}}, state)
    reset = plan({"platform": {"settings": {BATCH: None}}}, state)
    changed = plan({"platform": {"settings": {BATCH: 60}}}, state)

    assert same.changes == ()
    assert [(c.action, c.address) for c in reset.changes] == [(ChangeAction.DELETE, (BATCH,))]
    assert changed.changes[0].action is ChangeAction.UPDATE


@pytest.mark.parametrize(
    "key", ["log_level", "smtp_password", "time_format", "no_such_setting", LOGIN_KEY]
)
def test_a_key_the_portal_does_not_let_the_superuser_set_is_refused(key: str) -> None:
    result = plan({"platform": {"settings": {key: "x"}}})

    assert refusals(result) == [(f"platform.settings[{key}]", ErrorCode.SETTING_NOT_EDITABLE, key)]
    assert editable_setting(key) is None


def test_a_value_of_the_wrong_type_or_outside_its_bounds_is_refused_by_key() -> None:
    for value in (0, 5000, "10", True, 1.5):
        result = plan({"platform": {"settings": {BATCH: value}}})
        assert refusals(result) == [
            (f"platform.settings[{BATCH}]", ErrorCode.SETTING_INVALID, BATCH)
        ]


def test_production_refuses_the_values_it_never_uses() -> None:
    data = {"platform": {"settings": {"update_channel": "rc"}}}

    assert plan(data).ok
    refused = plan(data, context=RuleContext(production=True))
    assert refused.refusals[0].code is ErrorCode.SETTING_INVALID


def test_a_setting_the_environment_locks_cannot_change_but_may_be_restated() -> None:
    state = current(platform(settings=(entity("platform_setting", (BATCH,), {"value": 50}),)))
    usage = Usage(locked_settings=frozenset({BATCH}))

    changed = plan({"platform": {"settings": {BATCH: 60}}}, state, usage=usage)
    reset = plan({"platform": {"settings": {BATCH: None}}}, state, usage=usage)
    same = plan({"platform": {"settings": {BATCH: 50}}}, state, usage=usage)

    assert refusals(changed) == [(f"platform.settings[{BATCH}]", ErrorCode.SETTING_LOCKED, BATCH)]
    assert [r.code for r in reset.refusals] == [ErrorCode.SETTING_LOCKED]
    assert same.ok


def test_settings_that_break_a_rule_together_are_refused_when_one_changes() -> None:
    seen: list[Mapping[str, object]] = []

    def problem(values: Mapping[str, object]) -> str | None:
        seen.append(dict(values))
        return "reminder days" if values.get(BATCH) == 77 else None

    context = RuleContext(combination_problem=problem)
    state = current(platform(settings=(entity("platform_setting", (RATE,), {"value": "5/hour"}),)))

    refused = plan({"platform": {"settings": {BATCH: 77}}}, state, context=context)
    fine = plan({"platform": {"settings": {BATCH: 78}}}, state, context=context)
    untouched = plan({"platform": {"api": {"enabled": True}}}, state, context=context)

    assert refusals(refused) == [("platform.settings", ErrorCode.SETTING_COMBINATION, "settings")]
    assert fine.ok
    assert untouched.ok
    assert seen[0] == {RATE: "5/hour", BATCH: 77}
    assert len(seen) == 2


def test_the_login_suffix_may_not_overlap_a_claimed_domain_either_way() -> None:
    claimed = entity(
        "organisation",
        ("acme",),
        {},
        {
            "identity_providers": (
                entity(
                    "identity_provider",
                    ("p",),
                    {},
                    {"domains": (entity("identity_provider_domain", ("example.nl",)),)},
                ),
            )
        },
    )
    state = current(organisations=(claimed,))

    overlap = plan({"platform": {"settings": {SUFFIX: "login.example.nl"}}}, state)
    apart = plan({"platform": {"settings": {SUFFIX: "login.other.nl"}}}, state)
    claim = plan(
        {
            "organisations": [
                {
                    "slug": "acme",
                    "identity_providers": [
                        {"key": "p", "domains": ["example.nl", "corp.openplanner.internal"]}
                    ],
                }
            ]
        },
        state,
    )
    claim_ok = plan(
        {
            "organisations": [
                {
                    "slug": "acme",
                    "identity_providers": [{"key": "p", "domains": ["example.nl", "other.nl"]}],
                }
            ]
        },
        state,
    )

    assert refusals(overlap) == [
        (f"platform.settings[{SUFFIX}]", ErrorCode.IDP_DOMAIN_TAKEN, SUFFIX)
    ]
    assert apart.ok
    assert [r.code for r in claim.refusals] == [ErrorCode.IDP_DOMAIN_TAKEN]
    assert claim_ok.ok


def test_the_suffix_in_force_is_the_one_in_the_context_when_the_document_sets_none() -> None:
    context = RuleContext(settings={SUFFIX: "internal.example.nl"})
    data = {
        "organisations": [
            {"slug": "acme", "identity_providers": [{"key": "p", "domains": ["example.nl"]}]}
        ]
    }

    result = plan(
        data, current(organisations=(entity("organisation", ("acme",)),)), context=context
    )

    assert [r.code for r in result.refusals] == [ErrorCode.IDP_DOMAIN_TAKEN]


# --- secrets -----------------------------------------------------------------------------


def test_the_secret_names_come_from_the_registry_and_the_service_app() -> None:
    assert SECRET_NAMES == {
        "smtp_password",
        "webex_client_secret",
        "webex_refresh_token",
        "update_check_token",
        "webex_service_app",
    }


def test_a_secret_is_checked_and_trimmed_once_opened() -> None:
    document = with_secret(
        parse({"platform": {"secrets": {"smtp_password": {"sealed": "YQ=="}}}}),
        "secrets",
        "smtp_password",
        Secret("  hunter22  "),
    )

    result = plan({}, document=document)

    (change,) = result.changes
    assert (change.kind, change.fields) == ("platform_secret", ("secret",))
    assert change.values["secret"] == Secret("hunter22")
    assert "hunter22" not in repr(result)


def test_a_secret_that_is_the_same_is_no_change() -> None:
    state = current(
        platform(secrets=(entity("platform_secret", ("smtp_password",), {"secret": Secret("x")}),))
    )
    document = with_secret(
        parse({"platform": {"secrets": {"smtp_password": {"sealed": "YQ=="}}}}),
        "secrets",
        "smtp_password",
        Secret("x"),
    )

    assert plan({}, state, document=document).changes == ()


def test_a_blank_secret_a_foreign_name_and_a_locked_secret_are_refused() -> None:
    sealed = {"sealed": "YQ=="}
    blank = with_secret(
        parse({"platform": {"secrets": {"smtp_password": sealed}}}),
        "secrets",
        "smtp_password",
        Secret("   "),
    )

    assert refusals(plan({}, document=blank)) == [
        ("platform.secrets[smtp_password]", ErrorCode.SETTING_INVALID, "smtp_password")
    ]
    assert plan({"platform": {"secrets": {"webex_service_app": sealed}}}).ok
    foreign = plan({"platform": {"secrets": {"session_secret": sealed}}})
    assert refusals(foreign) == [
        ("platform.secrets[session_secret]", ErrorCode.SETTING_NOT_EDITABLE, "session_secret")
    ]
    locked = plan(
        {"platform": {"secrets": {"smtp_password": sealed}}},
        usage=Usage(locked_settings=frozenset({"smtp_password"})),
    )
    assert [r.code for r in locked.refusals] == [ErrorCode.SETTING_LOCKED]


def test_a_secret_goes_back_to_json_sealed() -> None:
    data = {"platform": {"secrets": {"smtp_password": {"sealed": "YQ=="}}}}
    document = parse(data)

    (platform_doc,) = document.root.children["platform"]
    (entry,) = platform_doc.children["secrets"]
    assert entry.fields["secret"] == Sealed("YQ==")
    assert to_json(document)["platform"] == data["platform"]


# --- machine API -------------------------------------------------------------------------


def test_the_api_switch_has_the_api_permission_and_defaults_to_off() -> None:
    recorder = Recorder()

    result = plan({"platform": {"api": {"enabled": True}}}, may_change=recorder)

    (change,) = result.changes
    assert (change.kind, change.action, change.fields) == (
        "api_settings",
        ChangeAction.CREATE,
        ("enabled",),
    )
    assert change.values == {"enabled": True, "docs_public": False}
    assert recorder.asked == [(Permission.API_CLIENTS_MANAGE, Target.everything())]


def test_the_platform_itself_cannot_be_deleted() -> None:
    assert refusals(plan({"platform": {"action": "delete"}})) == [
        ("platform", ErrorCode.INVALID_CONFIGURATION, None)
    ]


# --- log destinations --------------------------------------------------------------------

HEC = {
    "name": "splunk",
    "kind": "splunk_hec",
    "address": "https://splunk.example.nl:8088/services/collector",
    "index_name": "openplanner",
    "source": "op",
    "secret": {"sealed": "YQ=="},
}


def plan_destination(entry: Mapping[str, Any], secret: Value | None = None, **kw: Any) -> Plan:
    document = parse({"platform": {"log_destinations": [entry]}})
    if secret is not None:
        document = with_secret(document, "log_destinations", entry["name"], secret)
    return plan({}, document=document, **kw)


def test_a_log_destination_is_cleaned_like_the_logging_screen_does() -> None:
    result = plan_destination(HEC, Secret(" token-1 "), context=RuleContext(production=True))

    (change,) = result.changes
    assert change.permissions == {Permission.LOGGING_MANAGE}
    assert change.values["secret"] == Secret("token-1")
    assert change.values["categories"] == "audit,security,system"
    assert change.values["min_level"] == "info"
    assert change.values["enabled"] is False


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"address": "ftp://x.example.nl"}, "address"),
        ({"address": "https://user:pw@x.example.nl"}, "address"),
        ({"categories": "audit,gossip"}, "categories"),
        ({"index_name": "bad index"}, "index"),
        ({"ca_pem": "PRIVATE KEY"}, "ca_pem"),
        ({"name": "a  b"}, "name"),
    ],
)
def test_a_log_destination_the_portal_would_refuse_is_refused(
    changes: dict[str, str], field: str
) -> None:
    result = plan_destination({**HEC, **changes}, Secret("token"))

    assert [(r.code, r.field) for r in result.refusals] == [
        (ErrorCode.LOG_DESTINATION_INVALID, field)
    ]


def test_plain_http_is_for_development_only() -> None:
    entry = {**HEC, "address": "http://splunk.example.nl:8088"}

    assert plan_destination(entry, Secret("t")).ok
    result = plan_destination(entry, Secret("t"), context=RuleContext(production=True))
    assert [r.code for r in result.refusals] == [ErrorCode.LOG_DESTINATION_INSECURE]


def test_a_destination_that_needs_a_credential_has_one_and_one_that_cannot_hold_one_has_none() -> (
    None
):
    without = {k: v for k, v in HEC.items() if k != "secret"}
    syslog = {"name": "sys", "kind": "syslog", "address": "syslog+tls://log.example.nl:6514"}

    needs = plan_destination(without)
    stored = plan(
        {"platform": {"log_destinations": [without]}},
        current(
            platform(
                log_destinations=(
                    entity(
                        "log_destination",
                        ("splunk",),
                        {
                            **{k: v for k, v in HEC.items() if k not in ("name", "secret")},
                            "secret": Secret("old"),
                        },
                    ),
                )
            )
        ),
    )
    refused_secret = plan_destination(syslog, Secret("token"))

    assert [(r.code, r.field) for r in needs.refusals] == [
        (ErrorCode.LOG_DESTINATION_INVALID, "secret")
    ]
    assert stored.ok
    assert plan_destination(syslog).ok
    assert [(r.code, r.field) for r in refused_secret.refusals] == [
        (ErrorCode.LOG_DESTINATION_INVALID, "secret")
    ]


def test_a_stored_destination_of_an_unknown_kind_is_refused_by_the_rule() -> None:
    merged = {"name": "x", "kind": "pager", "address": "https://x.example.nl"}

    assert LOG_DESTINATION.check is not None
    with pytest.raises(Exception) as error:  # noqa: PT011 - the code is asserted below
        LOG_DESTINATION.check(merged, None, RuleContext())
    assert getattr(error.value, "code", None) is ErrorCode.LOG_DESTINATION_INVALID


# --- roles -------------------------------------------------------------------------------

BASE_PERMISSIONS = sorted(p.value for p in MATRIX[Role.GROUP_ADMIN])
ROLE_DOC = {
    "claim_value": "planners",
    "template": "group_admin",
    "name_nl": "  Planners  ",
    "name_en": "Planners",
    "permissions": [*reversed(BASE_PERMISSIONS), "group.edit"],
}


def test_a_role_is_built_like_the_role_editor_builds_it() -> None:
    recorder = Recorder()

    result = plan({"platform": {"roles": [ROLE_DOC]}}, may_change=recorder)

    (change,) = result.changes
    assert result.ok
    assert change.values["permissions"] == sorted([*BASE_PERMISSIONS, "group.edit"])
    assert change.values["name_nl"] == "Planners"
    assert recorder.asked == [(Permission.ROLES_MANAGE, Target.everything())]


@pytest.mark.parametrize(
    ("changes", "code", "field"),
    [
        (
            {"permissions": [*BASE_PERMISSIONS, "roles.manage"]},
            ErrorCode.ROLE_INVALID,
            "permissions",
        ),
        (
            {"permissions": [p for p in BASE_PERMISSIONS if p != "dashboard.view"]},
            ErrorCode.ROLE_INVALID,
            "permissions",
        ),
        (
            {"permissions": [*BASE_PERMISSIONS, "no.such"]},
            ErrorCode.INVALID_CONFIGURATION,
            "permissions",
        ),
        ({"template": "wizard"}, ErrorCode.INVALID_CONFIGURATION, "permissions"),
        ({"claim_value": "bad claim"}, ErrorCode.INVALID_CONFIGURATION, "claim_value"),
        ({"claim_value": "Superuser-Role"}, ErrorCode.CLAIM_VALUE_TAKEN, "claim_value"),
    ],
)
def test_a_role_the_editor_would_refuse_is_refused(
    changes: dict[str, Any], code: ErrorCode, field: str
) -> None:
    context = RuleContext(built_in_claim_values=frozenset({"superuser-role"}))
    doc = {**ROLE_DOC, "permissions": BASE_PERMISSIONS, **changes}

    result = plan({"platform": {"roles": [doc]}}, context=context)

    assert [(r.code, r.field) for r in result.refusals] == [(code, field)]


def test_a_role_name_is_bounded_when_the_document_is_read() -> None:
    with pytest.raises(DomainError) as error:
        parse({"platform": {"roles": [{**ROLE_DOC, "name_en": "x" * 81}]}})

    assert error.value.field == "platform.roles[planners].name_en"


def test_the_template_of_an_existing_role_cannot_change() -> None:
    state = current(
        platform(
            roles=(
                entity(
                    "role",
                    ("planners",),
                    {
                        **{k: v for k, v in ROLE_DOC.items() if k != "claim_value"},
                        "name_nl": "Planners",
                    },
                ),
            )
        )
    )

    same = plan({"platform": {"roles": [ROLE_DOC]}}, state)
    moved = plan({"platform": {"roles": [{**ROLE_DOC, "template": "org_admin"}]}}, state)

    assert same.ok
    assert refusals(moved) == [
        ("platform.roles[planners]", ErrorCode.INVALID_CONFIGURATION, "template")
    ]


# --- holiday calendars -------------------------------------------------------------------


def test_a_calendar_and_its_days_are_created_with_the_holiday_permission() -> None:
    recorder = Recorder()
    data = {
        "platform": {
            "holiday_calendars": [
                {
                    "slug": "nl",
                    "name": "Nederland",
                    "days": {"2026-12-25": "Eerste kerstdag", "2026-12-26": "Tweede kerstdag"},
                }
            ]
        }
    }

    result = plan(data, may_change=recorder)

    assert [(c.kind, c.address) for c in result.changes] == [
        ("holiday_calendar", ("nl",)),
        ("holiday", ("nl", "2026-12-25")),
        ("holiday", ("nl", "2026-12-26")),
    ]
    assert {p for p, _ in recorder.asked} == {Permission.HOLIDAYS_MANAGE}
    assert to_json(parse(data))["platform"] == data["platform"]


@pytest.mark.parametrize("day", ["2026-2-3", "2026-13-01", "25-12-2026", "kerst"])
def test_a_day_must_be_an_iso_date_in_full(day: str) -> None:
    data = {"platform": {"holiday_calendars": [{"slug": "nl", "name": "NL", "days": {day: "X"}}]}}

    result = plan(data)

    assert any(r.code is ErrorCode.INVALID_CONFIGURATION for r in result.refusals)


def test_a_calendar_needs_a_slug_and_a_name_like_an_organisation_does() -> None:
    bad_slug = plan({"platform": {"holiday_calendars": [{"slug": "Bad Slug", "name": "X"}]}})
    bad_name = plan({"platform": {"holiday_calendars": [{"slug": "nl", "name": "   "}]}})
    bad_day_name = plan(
        {
            "platform": {
                "holiday_calendars": [{"slug": "nl", "name": "NL", "days": {"2026-12-25": "  "}}]
            }
        }
    )

    assert refusals(bad_slug)[0][1:] == (ErrorCode.INVALID_CONFIGURATION, "slug")
    assert refusals(bad_name)[0][1] is ErrorCode.INVALID_CONFIGURATION
    assert refusals(bad_day_name)[0][1] is ErrorCode.INVALID_CONFIGURATION


def test_a_calendar_still_named_by_an_organisation_or_group_is_not_deleted() -> None:
    calendar = entity("holiday_calendar", ("nl",), {"name": "NL"})
    group = entity("group", ("team",), {"holiday_calendars": ["nl"]})
    org = entity("organisation", ("acme",), {"holiday_calendars": []}, {"groups": (group,)})
    state = current(platform(holiday_calendars=(calendar,)), (org,))
    delete = {"platform": {"holiday_calendars": [{"slug": "nl", "action": "delete"}]}}

    refused = plan(delete, state)
    released = plan(
        {
            **delete,
            "organisations": [
                {"slug": "acme", "groups": [{"slug": "team", "holiday_calendars": []}]}
            ],
        },
        state,
    )
    unused = plan(delete, current(platform(holiday_calendars=(calendar,))))

    assert refusals(refused) == [
        ("platform.holiday_calendars[nl]", ErrorCode.HOLIDAY_CALENDAR_IN_USE, None)
    ]
    assert released.ok
    assert unused.ok


def test_an_organisation_naming_the_calendar_blocks_its_deletion_too() -> None:
    calendar = entity("holiday_calendar", ("nl",), {"name": "NL"})
    org = entity("organisation", ("acme",), {"holiday_calendars": ["nl"]})

    result = plan(
        {"platform": {"holiday_calendars": [{"slug": "nl", "action": "delete"}]}},
        current(platform(holiday_calendars=(calendar,)), (org,)),
    )

    assert [r.code for r in result.refusals] == [ErrorCode.HOLIDAY_CALENDAR_IN_USE]


# --- coverage of the tables --------------------------------------------------------------


def test_every_platform_table_is_covered_and_no_column_is_left_over() -> None:
    kinds = list(Format("x", ROOT).kinds())
    names = {k.name for k in kinds}

    assert {
        "platform",
        "platform_setting",
        "platform_secret",
        "api_settings",
        "log_destination",
        "role",
        "holiday_calendar",
        "holiday",
    } <= names
    assert Format(
        "x", Kind("r", collections=(Collection("platform", PLATFORM, Shape.SINGLE),))
    ).tables() == {
        "settings_version",
        "platform_settings",
        "integration_credentials",
        "api_settings",
        "log_destinations",
        "roles",
        "holiday_calendars",
        "holidays",
    }
    assert (
        uncovered_columns(
            [PLATFORM, PLATFORM_SETTING, PLATFORM_SECRET, LOG_DESTINATION, ROLE, HOLIDAY_CALENDAR]
            + [k for k in kinds if k.name in ("api_settings", "holiday")]
        )
        == {}
    )


def test_a_day_longer_than_a_date_is_refused_when_the_document_is_read() -> None:
    data = {
        "platform": {
            "holiday_calendars": [{"slug": "nl", "name": "NL", "days": {"2026-12-25T00": "X"}}]
        }
    }

    with pytest.raises(DomainError):
        parse(data)
