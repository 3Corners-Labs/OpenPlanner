"""The descriptor registry and the stored attributes of a group routing (ADR 0032)."""

import sqlite3

import pytest

from app.adapters import contact_centers
from app.adapters.contact_centers import (
    DESCRIPTORS,
    attributes_from_json,
    attributes_json,
    routing,
)
from app.adapters.db.configuration_package import _check_contact_center_rows
from app.adapters.wxcc import DESCRIPTOR
from app.domain.contact_center import ContactCenterKind
from app.domain.errors import DomainError, ErrorCode


def test_every_kind_has_its_adapters_descriptor() -> None:
    assert set(DESCRIPTORS) == set(ContactCenterKind)
    assert DESCRIPTORS[ContactCenterKind.WEBEX_CC] is DESCRIPTOR


def test_attributes_round_trip_as_a_sorted_object() -> None:
    text = attributes_json({"channel": "c", "business_address": "a"})

    assert text == '{"business_address":"a","channel":"c"}'
    assert attributes_from_json(text) == {"business_address": "a", "channel": "c"}


@pytest.mark.parametrize("text", ["not json", "[]", '{"channel": 1}', '"text"'])
def test_stored_attributes_are_parsed_strictly(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - JSONDecodeError or our own refusal
        attributes_from_json(text)


def test_empty_values_make_no_routing() -> None:
    assert routing(ContactCenterKind.WEBEX_CC, None, {"channel": None}) is None


def _package(*rows: tuple[str, str | None, str]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE group_contact_center (group_id INTEGER, kind TEXT, routing_value TEXT, "
        "attributes TEXT)"
    )
    db.executemany(
        "INSERT INTO group_contact_center VALUES (1, ?, ?, ?)",
        rows,
    )
    return db


def test_an_imported_routing_the_descriptor_accepts_passes() -> None:
    _check_contact_center_rows(_package(("webex_cc", "team", '{"channel":"c"}')))


@pytest.mark.parametrize(
    "row",
    [
        ("other_cc", None, "{}"),
        ("webex_cc", "r" * 513, "{}"),
        ("webex_cc", None, '{"unknown":"x"}'),
        ("webex_cc", None, '{"channel":1}'),
    ],
)
def test_an_imported_routing_the_descriptor_refuses_fails_the_import(
    row: tuple[str, str | None, str],
) -> None:
    with pytest.raises(DomainError) as error:
        _check_contact_center_rows(_package(row))

    assert error.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID


def test_every_platform_has_a_descriptor_and_a_webhook_handling() -> None:
    """Adding a platform adds both; one without the other is a broken registration."""
    assert set(contact_centers.INBOUND) == set(contact_centers.DESCRIPTORS)
    for kind, handling in contact_centers.INBOUND.items():
        assert handling.kind is kind
        assert handling.path == f"/webhooks/contact-center/{kind.value}"
        assert contact_centers.inbound(kind.value) is handling


def test_wxcc_keeps_its_old_address_and_its_event_source() -> None:
    """ADR 0032: event keys stored before the neutral route still match; the alias is ours."""
    wxcc = contact_centers.INBOUND[ContactCenterKind.WEBEX_CC]
    assert wxcc.source == "wxcc"
    assert wxcc.registered_path == "/webhooks/wxcc"
    assert wxcc.paths == ("/webhooks/contact-center/webex_cc", "/webhooks/wxcc")


def test_an_unknown_or_malformed_kind_has_no_handling() -> None:
    assert contact_centers.inbound("genesys") is None
    assert contact_centers.inbound("WEBEX_CC") is None
    assert contact_centers.inbound("") is None
