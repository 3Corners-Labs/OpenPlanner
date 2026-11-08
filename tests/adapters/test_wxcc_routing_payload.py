"""The contact-center payload with routing variables (ADR 0081 point 6, CLAUDE.md privacy).

The dispatched task carries the reference, first name, start label, group name, host join URL,
kind and the mapped routing variables, nothing else of the booking; every routing value is
one the group's mapping wrote for one of its subject codes or one of the catalogue's codes.
"""

import json
from datetime import timedelta

from app.adapters.wxcc import ROUTING_KEY, task_body
from app.domain.booking import Booking, BookingStatus, Contact, MeetingRef
from app.domain.calls import contact_center_task
from app.domain.form_fields import Answers
from app.domain.routing import (
    ATTRIBUTE_VALUES,
    SUBJECT_SOURCE,
    Attribute,
    GroupRouting,
    RouteVariable,
    RoutingChoice,
    Subject,
)
from app.domain.scheduling import PhoneMode
from tests.support.domain import NOW, SLOT_0900, make_group, webex_routing

ROUTING = GroupRouting(
    (
        Subject.create("general", {"nl": "Algemene vraag", "en": "General question"}),
        Subject.create("move", {"nl": "Verhuizing doorgeven", "en": "Report a move"}),
    ),
    (Attribute.PREFERRED_LANGUAGE, Attribute.CHANNEL_PREFERENCE),
    (
        (SUBJECT_SOURCE, RouteVariable("topic", (("general", "GEN"), ("move", "MOVE")))),
        ("preferred_language", RouteVariable("lang", (("en", "en"), ("nl", "nl")))),
        # channel_preference is switched on but not mapped: it must not be sent.
    ),
)
GROUP = make_group(
    slug="demo",
    name="Demo",
    video_host_email="host@example.nl",
    contact_center=webex_routing("openplanner@example.biz", "openplanner-video"),
    routing=ROUTING,
)
CONTACT = Contact.create(
    first_name="Ada",
    last_name="Lovelace",
    email="ada@example.org",
    phone="+31612345678",
    phone_mode=PhoneMode.OPTIONAL,
    answers=Answers(1, {"note": "secret-free-text"}, frozenset()),
)
BOOKING = Booking(
    id=123,
    group_id=1,
    slot_id=7,
    start=SLOT_0900,
    end=SLOT_0900 + timedelta(minutes=30),
    status=BookingStatus.CONFIRMED,
    contact=CONTACT,
    locale="nl",
    token_hash="h",
    created_at=NOW,
    meeting=MeetingRef("m-1", "https://site.webex.com/meet/abc"),
    routing=RoutingChoice("move", (("channel_preference", "phone"), ("preferred_language", "en"))),
)


def _body() -> dict[str, object]:
    item = contact_center_task(BOOKING, GROUP, start_label="5 jan 2026, 09:00")
    return task_body(item, "alias-1", 1767600000000)


def test_the_payload_carries_the_mapped_routing_variables_next_to_the_group() -> None:
    assert _body()["flowSettings"] == {ROUTING_KEY: "demo", "topic": "MOVE", "lang": "en"}


def test_every_routing_value_is_a_mapped_code_of_the_group_or_the_catalogue() -> None:
    settings = dict(_body()["flowSettings"])  # type: ignore[call-overload]
    settings.pop(ROUTING_KEY)
    allowed: dict[str, set[str]] = {}
    for source, variable in ROUTING.mapping:
        codes = (
            {s.code for s in ROUTING.subjects}
            if source == SUBJECT_SOURCE
            else set(ATTRIBUTE_VALUES[Attribute(source)])
        )
        allowed[variable.name] = {value for code, value in variable.values if code in codes}
    assert settings
    for name, value in settings.items():
        assert value in allowed[name]


def test_no_other_booking_field_or_label_reaches_the_payload() -> None:
    text = json.dumps(_body())
    for leaked in (
        "Lovelace",
        "ada@example.org",
        "+31612345678",
        "secret-free-text",
        "Verhuizing doorgeven",
        "Report a move",
        "phone",  # the unmapped attribute's code
        "channel_preference",
    ):
        assert leaked not in text
    data = _body()["channelParams"]["message"]["workItemData"]  # type: ignore[index]
    assert set(data) == {
        "bookingReference",
        "firstName",
        "startTime",
        "groupName",
        "meetingLink",
        "kind",
    }


def test_the_routing_key_of_openplanner_cannot_be_overwritten() -> None:
    # The mapping refuses the reserved name, and the body puts the group's value last anyway.
    item = contact_center_task(BOOKING, GROUP, start_label="x")
    forced = type(item)(**{**item.__dict__, "variables": ((ROUTING_KEY, "other"),)})
    assert task_body(forced, "a", 1)["flowSettings"] == {ROUTING_KEY: "demo"}


def test_a_booking_without_a_choice_sends_only_the_group() -> None:
    from dataclasses import replace

    item = contact_center_task(replace(BOOKING, routing=None), GROUP, start_label="x")
    assert task_body(item, "a", 1)["flowSettings"] == {ROUTING_KEY: "demo"}


def test_the_recorded_payload_holds_only_the_allowed_fields() -> None:
    """ADR 0032 point 8 as amended by ADR 0081 §6: every value in the request is one of the
    allowed fields, the group's routing attributes or a mapped routing variable."""
    body = _body()
    assert set(body) == {
        "origin",
        "destination",
        "channelType",
        "channel",
        "flowSettings",
        "channelParams",
    }

    def leaves(value: object) -> list[str]:
        if isinstance(value, dict):
            return [leaf for v in value.values() for leaf in leaves(v)]
        return [str(value)]

    allowed = {
        BOOKING.reference,
        "Ada",
        "5 jan 2026, 09:00",
        GROUP.name,
        "https://site.webex.com/meet/abc",
        "scheduled",
        "openplanner@example.biz",
        "openplanner-video",
        "businessAddress",
        "workItem",
        "work-item-form",
        "alias-1",
        "1767600000000",
        "demo",
        "MOVE",
        "en",
    }
    assert set(leaves(body)) <= allowed
