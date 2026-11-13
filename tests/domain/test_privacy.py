"""The fixed personal-data field list and its classification (ADR 0063 §1)."""

import dataclasses

import pytest

from app.domain.privacy import (
    ALIASES,
    DATA_FIELDS,
    DERIVED_FIELDS,
    ENTITIES,
    ENTITY,
    PII_FIELDS,
    PURGED_FIELDS,
    REQUEST_DATA_FIELDS,
    REQUEST_DERIVED_FIELDS,
    REQUEST_ENTITY,
    ROUTING_FIELDS,
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
    "form_answers": "sensitive",
    "guest_join_url": "sensitive",
    "host_join_url": "sensitive",
    "trial_join_url": "sensitive",
}


def test_the_field_list_is_exactly_the_one_of_the_privacy_rules() -> None:
    assert {f.name: f.classification.value for f in DATA_FIELDS} == EXPECTED
    assert PII_FIELDS == frozenset(EXPECTED)
    assert ENTITY == "Booking"


def test_the_list_keeps_the_card_order_direct_fields_first() -> None:
    assert [f.name for f in DATA_FIELDS] == list(EXPECTED)
    assert fields_of(Classification.DIRECT) == ("first_name", "last_name", "email", "phone")
    assert fields_of(Classification.SENSITIVE) == (
        "form_answers",
        "guest_join_url",
        "host_join_url",
        "trial_join_url",
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
    assert ALIASES == frozenset({"name", "answers", "note"})
    assert not ALIASES & PII_FIELDS


def test_the_list_cannot_be_changed_at_run_time() -> None:
    assert isinstance(DATA_FIELDS, tuple)
    assert isinstance(PII_FIELDS, frozenset)
    with pytest.raises(dataclasses.FrozenInstanceError):
        DATA_FIELDS[0].name = "other"  # type: ignore[misc]
    assert DataField("email", Classification.DIRECT) == DATA_FIELDS[2]


def test_note_is_no_personal_data_field_but_stays_masked_in_logs() -> None:
    """ADR 0102 point 13: the column is gone; the name stays an alias so a log line is masked."""
    assert "note" not in PII_FIELDS
    assert "note" in ALIASES


def test_the_purge_clears_the_personal_fields_and_the_provider_links() -> None:
    """ADR 0102 point 11: the one list the purge works from."""
    assert DERIVED_FIELDS == (
        "video_room_id",
        "trial_room_id",
        "contact_center_task_id",
        "wxcc_task_id",
        "claimed_by",
    )
    assert set(PURGED_FIELDS) == PII_FIELDS | set(DERIVED_FIELDS) | set(ROUTING_FIELDS)
    assert len(PURGED_FIELDS) == len(set(PURGED_FIELDS))


def test_the_kind_of_a_task_is_deliberately_not_purged() -> None:
    """A closed-list word that finds nothing at the provider: the task's id is what is purged."""
    kept = {"contact_center_task_kind", "contact_center_attempt_kind", "contact_center_kind"}

    assert kept.isdisjoint(PURGED_FIELDS)
    assert kept.isdisjoint(PII_FIELDS)
    assert {"contact_center_task_id", "wxcc_task_id"} <= set(PURGED_FIELDS)


def test_the_request_record_uses_the_booking_names_so_one_list_masks_both() -> None:
    """ADR 0121 decision D1: the request-level record is a subset of the list, by name."""
    assert REQUEST_DATA_FIELDS == ("first_name", "last_name", "email", "phone", "form_answers")
    assert set(REQUEST_DATA_FIELDS) <= PII_FIELDS
    assert REQUEST_DERIVED_FIELDS == ("assigned_to",)
    assert ENTITIES == (ENTITY, REQUEST_ENTITY) == ("Booking", "ServiceRequest")
