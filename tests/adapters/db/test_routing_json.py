"""The stored form of a group's routing (ADR 0081): read entry by entry, never erased."""

import json

from app.adapters.db.routing_json import (
    choice_from_columns,
    choice_json,
    routing_from_json,
    routing_json,
    subjects_column,
    subjects_from_column,
)
from app.domain.routing import SUBJECT_SOURCE, Attribute, GroupRouting, RouteVariable, Subject

ROUTING = GroupRouting(
    (Subject.create("move", {"nl": "Verhuizing", "en": "Moving"}),),
    (Attribute.PREFERRED_LANGUAGE,),
    (
        (SUBJECT_SOURCE, RouteVariable("topic", (("move", "MV"),))),
        ("preferred_language", RouteVariable("lang", (("en", "en"),))),
    ),
)


def test_a_routing_round_trips() -> None:
    assert routing_from_json(routing_json(ROUTING)) == ROUTING
    assert routing_json(GroupRouting()) is None
    assert routing_from_json(None) == GroupRouting()


def test_an_entry_that_no_longer_validates_is_skipped_and_the_rest_kept() -> None:
    body = json.loads(routing_json(ROUTING) or "")
    body["attributes"].append("shoe_size")  # an attribute this release does not know
    body["subjects"].append({"code": "Bad Code", "labels": {"nl": "x", "en": "y"}})
    body["subjects"].append({"code": "move", "labels": {"nl": "Dubbel", "en": "Twice"}})
    body["mapping"]["shoe_size"] = {"name": "shoe", "values": {}}
    body["mapping"][SUBJECT_SOURCE]["values"]["gone"] = "GONE"
    assert routing_from_json(json.dumps(body)) == ROUTING
    assert routing_from_json("not json") == GroupRouting()
    assert routing_from_json("[]") == GroupRouting()


def test_choices_and_block_subjects_round_trip() -> None:
    choice = choice_from_columns("move", choice_json(choice_from_columns("move", '{"a": "b"}')))
    assert choice is not None
    assert (choice.subject, choice.attributes) == ("move", (("a", "b"),))
    assert choice_from_columns(None, None) is None
    assert choice_from_columns("x", "broken") == choice_from_columns("x", None)
    assert subjects_from_column(subjects_column(("a", "b"))) == ("a", "b")
    assert subjects_column(()) is None
