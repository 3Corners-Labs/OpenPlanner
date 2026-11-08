"""A group's contact-center routing as a JSON object (ADR 0032, ADR 0099)."""

import pytest

from app.adapters.wxcc import DESCRIPTOR
from app.domain.contact_center_codec import contact_center_from_json, contact_center_to_json
from app.domain.errors import DomainError, ErrorCode

DESCRIPTORS = {DESCRIPTOR.kind.value: DESCRIPTOR}


def test_nothing_is_no_routing() -> None:
    assert contact_center_from_json(None, DESCRIPTORS) is None
    assert contact_center_from_json({"kind": "webex_cc"}, DESCRIPTORS) is None
    assert contact_center_to_json(None) is None


def test_a_routing_round_trips_through_its_json() -> None:
    value = {"kind": "webex_cc", "routing_value": "team-42", "attributes": {"channel": "video"}}

    routing = contact_center_from_json(value, DESCRIPTORS)

    assert routing is not None
    assert routing.routing_value == "team-42"
    assert contact_center_to_json(routing) == value


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("webex_cc", "contact_center"),
        ({"kind": "webex_cc", "extra": 1}, "contact_center"),
        ({"kind": 1}, "contact_center.kind"),
        ({"kind": "nope"}, "contact_center.kind"),
        ({"kind": "webex_cc", "routing_value": 3}, "contact_center.routing_value"),
        ({"kind": "webex_cc", "attributes": []}, "contact_center.attributes"),
        ({"kind": "webex_cc", "attributes": {"channel": 3}}, "contact_center.attributes"),
        ({"kind": "webex_cc", "attributes": {"zzz": "x"}}, "contact_center.attributes"),
        ({"kind": "webex_cc", "attributes": {"channel": ""}}, "contact_center.channel"),
    ],
)
def test_a_value_the_platform_refuses_names_the_part_that_is_wrong(
    value: object, field: str
) -> None:
    with pytest.raises(DomainError) as refused:
        contact_center_from_json(value, DESCRIPTORS)

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == field
