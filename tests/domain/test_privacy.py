"""The fixed personal-data field list and its classification (ADR 0063 §1)."""

import dataclasses

import pytest

from app.domain.privacy import (
    ALIASES,
    DATA_FIELDS,
    ENTITY,
    PII_FIELDS,
    Classification,
    DataField,
    classification,
    fields_of,
    is_pii,
)

# Written by hand from CLAUDE.md (privacy rules), not derived from the module under test.
EXPECTED = {
    "first_name": "direct",
    "last_name": "direct",
    "email": "direct",
    "phone": "direct",
    "note": "sensitive",
    "form_answers": "sensitive",
    "guest_join_url": "sensitive",
    "host_join_url": "sensitive",
}


def test_the_field_list_is_exactly_the_one_of_the_privacy_rules() -> None:
    assert {f.name: f.classification.value for f in DATA_FIELDS} == EXPECTED
    assert PII_FIELDS == frozenset(EXPECTED)
    assert ENTITY == "Booking"


def test_the_list_keeps_the_card_order_direct_fields_first() -> None:
    assert [f.name for f in DATA_FIELDS] == list(EXPECTED)
    assert fields_of(Classification.DIRECT) == ("first_name", "last_name", "email", "phone")
    assert fields_of(Classification.SENSITIVE) == (
        "note",
        "form_answers",
        "guest_join_url",
        "host_join_url",
    )


@pytest.mark.parametrize(("field", "kind"), list(EXPECTED.items()))
def test_every_field_is_pii_with_its_classification(field: str, kind: str) -> None:
    assert is_pii(field)
    assert classification(field) is Classification(kind)


@pytest.mark.parametrize("field", ["id", "status", "reference", "slot_id", "", "Email", "name"])
def test_anything_else_is_not_pii_and_has_no_classification(field: str) -> None:
    assert not is_pii(field)
    with pytest.raises(ValueError, match="not a personal-data field"):
        classification(field)


def test_the_aliases_are_the_names_the_same_data_travels_by() -> None:
    assert ALIASES == frozenset({"name", "answers"})
    assert not ALIASES & PII_FIELDS


def test_the_list_cannot_be_changed_at_run_time() -> None:
    assert isinstance(DATA_FIELDS, tuple)
    assert isinstance(PII_FIELDS, frozenset)
    with pytest.raises(dataclasses.FrozenInstanceError):
        DATA_FIELDS[0].name = "other"  # type: ignore[misc]
    assert DataField("email", Classification.DIRECT) == DATA_FIELDS[2]
