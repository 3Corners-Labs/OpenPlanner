"""Checks the kinds of the version-1 format share (ADR 0099 point 7)."""

import pytest

from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import (
    contacts_check,
    effective_idp_key,
    int_setting,
    setting_value,
    strings_of,
    text_map,
    text_of,
    text_setting,
    utc_moment,
)
from app.domain.contacts import (
    ContactBlock,
    ContactKind,
    ContactOption,
    block_from_json,
    block_to_json,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.settings_registry import BY_KEY

CONTEXT = RuleContext()
PHONE = {"kind": "phone", "value": "030 123 4567", "labels": {"nl": "Klantenservice"}, "hours": {}}


def test_a_contact_block_is_written_the_way_it_is_stored() -> None:
    block = ContactBlock(
        (ContactOption.create(ContactKind.PHONE, "030 123 4567", labels={"nl": "Service"}),),
        {"nl": "Bel ons"},
    )

    assert block_to_json(block) == {
        "headings": {"nl": "Bel ons"},
        "options": [
            {"kind": "phone", "value": "030 123 4567", "labels": {"nl": "Service"}, "hours": {}}
        ],
    }
    assert block_from_json(block_to_json(block)) == block


def test_a_contact_block_is_read_strictly_with_the_row_of_the_offence() -> None:
    bad_url = {"options": [PHONE, {"kind": "website", "value": "http://x.nl"}]}

    with pytest.raises(DomainError) as error:
        block_from_json(bad_url)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert error.value.field == "contact_2_url"


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"extra": 1},
        {"headings": []},
        {"headings": {"nl": 1}},
        {"options": {}},
        {"options": ["phone"]},
        {"options": [{"kind": "fax", "value": "1"}]},
        {"options": [{"kind": "phone", "value": 5}]},
        {"options": [{"kind": "phone", "value": "030 123 4567", "extra": 1}]},
        {"options": [{"kind": "phone", "value": "030 123 4567", "labels": {"nl": 1}}]},
        {"options": [PHONE] * 7},
        {"headings": {"nl": "x" * 121}},
    ],
)
def test_a_contact_block_the_portal_would_refuse_is_refused(data: object) -> None:
    with pytest.raises(DomainError) as error:
        block_from_json(data)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


def test_the_contacts_check_returns_the_normalised_block_and_none_for_no_block() -> None:
    value = {"options": [{**PHONE, "value": "  030 123 4567 "}]}

    checked = contacts_check(value, CONTEXT)

    assert isinstance(checked, dict)
    assert checked["options"][0]["value"] == "030 123 4567"  # type: ignore[index]
    assert contacts_check({"options": []}, CONTEXT) is None
    assert contacts_check({"headings": {"nl": "Alleen een kop"}}, CONTEXT) is None


def test_the_contacts_check_refuses_what_is_not_a_block() -> None:
    with pytest.raises(DomainError) as error:
        contacts_check("not an object", CONTEXT)

    assert error.value.field == "contacts"


def test_a_setting_is_read_as_the_registry_types_it() -> None:
    assert setting_value(BY_KEY["staff_dormant_after_days"], 30) == 30
    assert setting_value(BY_KEY["time_format"], "12H") == "12h"
    assert setting_value(BY_KEY["product_footer"], False) is False
    assert setting_value(BY_KEY["rate_limit_bookings"], "5 / hour") == "5/hour"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("staff_dormant_after_days", 5),
        ("staff_dormant_after_days", 400),
        ("staff_dormant_after_days", "30"),
        ("staff_dormant_after_days", 30.0),
        ("staff_dormant_after_days", True),
        ("staff_dormant_after_days", None),
        ("product_footer", "yes"),
        ("product_footer", 1),
        ("time_format", "13h"),
        ("time_format", 24),
        ("rate_limit_bookings", "500/hour"),
        ("rate_limit_bookings", "5/day"),
        ("admin_timezone", "Mars/Olympus"),
    ],
)
def test_a_setting_outside_its_type_or_bounds_is_refused_by_key(key: str, value: object) -> None:
    with pytest.raises(DomainError) as error:
        setting_value(BY_KEY[key], value)  # type: ignore[arg-type]

    assert error.value.code is ErrorCode.SETTING_INVALID
    assert error.value.field == key


def test_a_value_that_production_never_uses_is_refused_only_there() -> None:
    spec = BY_KEY["update_channel"]

    assert setting_value(spec, "rc") == "rc"
    with pytest.raises(DomainError) as error:
        setting_value(spec, "rc", production=True)
    assert error.value.code is ErrorCode.SETTING_INVALID
    assert setting_value(spec, "release", production=True) == "release"


def test_a_secret_setting_is_not_a_plain_value() -> None:
    with pytest.raises(DomainError) as error:
        setting_value(BY_KEY["smtp_password"], "hunter2")

    assert error.value.code is ErrorCode.SETTING_INVALID


def test_the_small_readers_of_a_field_never_fail_on_what_is_not_there() -> None:
    values = {"a": "text", "b": 5, "c": ["x", 1, "y"], "d": "not a list"}

    assert (text_of(values, "a"), text_of(values, "b"), text_of(values, "zz")) == ("text", "", "")
    assert strings_of(values, "c") == ["x", "y"]
    assert strings_of(values, "d") == strings_of(values, "zz") == []
    assert effective_idp_key({"idp_key": "own"}, "slug") == "own"
    assert effective_idp_key({"idp_key": None}, "slug") == "slug"


def test_a_moment_is_read_with_its_offset_and_written_in_utc() -> None:
    assert utc_moment("2027-01-01T12:00:00+01:00", "when") == "2027-01-01T11:00:00+00:00"
    for bad in ("2027-01-01T12:00:00", "next year"):
        with pytest.raises(DomainError) as error:
            utc_moment(bad, "when")
        assert error.value.field == "when"


def test_a_map_of_texts_names_the_field_that_is_wrong() -> None:
    assert text_map({"nl": "een"}, "notices") == {"nl": "een"}
    with pytest.raises(DomainError) as whole:
        text_map(["nl"], "notices", code=ErrorCode.INVALID_FORM_FIELD)
    with pytest.raises(DomainError) as one:
        text_map({"nl": 1}, "notices", field_of=lambda locale: f"notice_{locale}")
    with pytest.raises(DomainError) as key:
        text_map({1: "x"}, "notices", field_of=lambda locale: f"notice_{locale}")

    assert (whole.value.code, whole.value.field) == (ErrorCode.INVALID_FORM_FIELD, "notices")
    assert one.value.field == "notice_nl"
    assert key.value.field == "notices"


def test_a_limit_comes_from_the_context_and_else_from_the_registry() -> None:
    limit = "idp_max_per_organisation"

    assert int_setting(CONTEXT, limit) == 2
    assert int_setting(RuleContext(settings={limit: 4}), limit) == 4
    assert int_setting(RuleContext(settings={limit: True}), limit) == 2
    assert text_setting(CONTEXT, "time_format") == "24h"
    assert text_setting(RuleContext(settings={"time_format": "12h"}), "time_format") == "12h"
