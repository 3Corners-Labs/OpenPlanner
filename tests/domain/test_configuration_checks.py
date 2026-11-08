"""Checks the kinds of the version-1 format share (ADR 0099 point 7)."""

import pytest

from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import contacts_check, setting_value
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
    spec = BY_KEY["update_auto_apply"]

    assert setting_value(spec, True) is True
    with pytest.raises(DomainError) as error:
        setting_value(spec, True, production=True)
    assert error.value.code is ErrorCode.SETTING_INVALID
    assert setting_value(spec, False, production=True) is False


def test_a_secret_setting_is_not_a_plain_value() -> None:
    with pytest.raises(DomainError) as error:
        setting_value(BY_KEY["smtp_password"], "hunter2")

    assert error.value.code is ErrorCode.SETTING_INVALID
