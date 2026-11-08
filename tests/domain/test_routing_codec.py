"""The strict JSON codec of a group's routing (ADR 0081, ADR 0099)."""

import json

import pytest

from app.adapters.db.routing_json import routing_from_json as stored_routing
from app.adapters.db.routing_json import routing_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import Attribute, GroupRouting, RouteVariable, Subject
from app.domain.routing_codec import routing_from_json, routing_to_json

ROUTING = GroupRouting(
    (
        Subject.create("billing", {"nl": "Factuur", "en": "Invoice"}),
        Subject.create("other", {"nl": "Anders", "en": "Other"}),
    ),
    (Attribute.PREFERRED_LANGUAGE,),
    (
        ("subject", RouteVariable("topic", (("billing", "B"), ("other", "O")))),
        ("preferred_language", RouteVariable("lang", (("nl", "dutch"),))),
    ),
)


def test_an_empty_routing_is_no_json() -> None:
    assert routing_to_json(GroupRouting()) is None


def test_routing_round_trips_and_equals_the_stored_form() -> None:
    written = routing_to_json(ROUTING)

    assert written is not None
    assert routing_from_json(written) == ROUTING
    assert json.loads(routing_json(ROUTING) or "") == written
    assert stored_routing(routing_json(ROUTING)) == ROUTING


def test_missing_parts_are_empty() -> None:
    assert routing_from_json({}) == GroupRouting()
    only = routing_from_json({"attributes": ["channel_preference"]})
    assert only.attributes == (Attribute.CHANNEL_PREFERENCE,)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"extra": 1},
        {"subjects": {}},
        {"subjects": ["billing"]},
        {"subjects": [{"code": "a"}]},
        {"subjects": [{"code": 1, "labels": {}}]},
        {"subjects": [{"code": "a", "labels": {"nl": 1}}]},
        {"subjects": [{"code": "a", "labels": []}]},
        {"attributes": "nl"},
        {"attributes": [1]},
        {"mapping": []},
        {"mapping": {"subject": "x"}},
        {"mapping": {"subject": {"values": {}}}},
        {"mapping": {"subject": {"name": "v", "values": {"a": 1}}}},
    ],
)
def test_a_wrong_shape_is_refused(data: object) -> None:
    with pytest.raises(DomainError) as error:
        routing_from_json(data)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


def test_an_unknown_attribute_is_refused_by_name() -> None:
    with pytest.raises(DomainError) as error:
        routing_from_json({"attributes": ["shoe_size"]})

    assert error.value.field == "attributes"


def test_what_the_routing_itself_refuses_is_refused() -> None:
    labels = {"nl": "A", "en": "A"}
    bad_code = {"subjects": [{"code": "Bad Code", "labels": labels}]}
    unmapped = {"mapping": {"subject": {"name": "v", "values": {}}}}

    for data, field in ((bad_code, "subject_code"), (unmapped, "mapping")):
        with pytest.raises(DomainError) as error:
            routing_from_json(data)
        assert error.value.field == field
