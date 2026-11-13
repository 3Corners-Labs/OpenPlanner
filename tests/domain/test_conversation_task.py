"""The conversation task in the channel-independent contract (ADR 0123 point 8, step 7a).

The kind belongs to the routing (default: the form), to the task built from it and, once the
platform made the task, to the reference the booking keeps. A platform that declares nothing
carries no conversation, and a routing cannot ask it for one.
"""

import pickle
from dataclasses import replace

import pytest

from app.adapters.wxcc import DESCRIPTOR
from app.domain.calls import ContactCenterTask, ParticipantMessage, contact_center_task
from app.domain.contact_center import (
    TASK_KIND,
    ContactCenterCapabilities,
    ContactCenterKind,
    ContactCenterRouting,
    ContactCenterTaskRef,
    ProviderDescriptor,
    TaskKind,
)
from app.domain.contact_center_codec import contact_center_from_json, contact_center_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.interaction import InteractionKind
from tests.support.domain import make_group, webex_routing
from tests.support.integration import make_booking

DESCRIPTORS = {DESCRIPTOR.kind.value: DESCRIPTOR}
BARE = ProviderDescriptor(
    ContactCenterKind.WEBEX_CC,
    "k",
    DESCRIPTOR.attributes,
    10,
    "k.value",
    capabilities=ContactCenterCapabilities(frozenset({InteractionKind.VIDEO})),
)


def test_the_kinds_are_a_closed_list_and_the_form_is_the_default() -> None:
    assert [kind.value for kind in TaskKind] == ["form", "conversation"]
    assert webex_routing().task_kind is TaskKind.FORM
    assert ContactCenterTaskRef("t", ContactCenterKind.WEBEX_CC).task_kind is TaskKind.FORM


def test_a_routing_holds_the_kind_it_was_given() -> None:
    routing = webex_routing(task_kind=TaskKind.CONVERSATION)

    assert routing.task_kind is TaskKind.CONVERSATION
    assert routing != webex_routing()
    assert hash(routing) != hash(webex_routing())
    assert pickle.loads(pickle.dumps(routing)) == routing  # noqa: S301 - our own object


def test_a_routing_cannot_ask_a_platform_for_a_kind_it_does_not_carry() -> None:
    with pytest.raises(DomainError) as refused:
        ContactCenterRouting(
            BARE, None, {"channel": "c", "business_address": "a"}, TaskKind.CONVERSATION
        )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == f"contact_center.{TASK_KIND}"


def test_a_task_is_of_the_routings_kind() -> None:
    group = make_group(
        id=1,
        contact_center=webex_routing(task_kind=TaskKind.CONVERSATION),
        video_host_email="host@example.nl",
    )
    booking = make_booking(meeting=None)

    task = contact_center_task(booking, group, start_label="x", interaction=InteractionKind.NONE)

    assert task.task_kind is TaskKind.CONVERSATION
    assert task.first_message == ""  # composed at the edge, by the caller
    form = contact_center_task(
        booking,
        replace(group, contact_center=webex_routing()),
        start_label="x",
        interaction=InteractionKind.NONE,
    )
    assert form.task_kind is TaskKind.FORM


def test_the_task_does_not_show_its_text_or_its_name() -> None:
    task = ContactCenterTask(
        reference="OP-1",
        first_name="Zoë",
        start_label="s",
        group_name="g",
        meeting_link="https://site.webex.com/m/1",
        kind="scheduled",
        routing=webex_routing(),
        routing_value="demo",
        task_kind=TaskKind.CONVERSATION,
        first_message="Zoë, https://site.webex.com/m/1",
    )

    assert "Zoë" not in repr(task)
    assert "site.webex.com" not in repr(task)


def test_a_participant_message_is_checked_and_never_shown() -> None:
    message = ParticipantMessage("msg-1", "Ik heb een vraag over mijn dossier")

    assert "dossier" not in repr(message)
    for message_id in ("", "a b", "x" * 65, "é"):
        with pytest.raises(DomainError) as refused:
            ParticipantMessage(message_id, "text")
        assert refused.value.field == "message_id"
    for text in ("", "  ", "x" * 20_001):
        with pytest.raises(DomainError) as refused:
            ParticipantMessage("m", text)
        assert refused.value.field == "text"


def test_the_document_holds_the_kind_only_when_it_is_not_the_default() -> None:
    form = {"kind": "webex_cc", "routing_value": "r", "attributes": {"channel": "c"}}
    conversation = {**form, "task_kind": "conversation"}

    parsed = contact_center_from_json(conversation, DESCRIPTORS)
    default = contact_center_from_json(form, DESCRIPTORS)

    assert parsed is not None
    assert default is not None
    assert (parsed.task_kind, default.task_kind) == (TaskKind.CONVERSATION, TaskKind.FORM)
    assert contact_center_to_json(parsed) == conversation
    assert contact_center_to_json(default) == form  # a document of a form routing is unchanged


@pytest.mark.parametrize("task_kind", ["chat", 3, None, ["form"]])
def test_an_import_with_another_kind_is_refused(task_kind: object) -> None:
    value = {"kind": "webex_cc", "routing_value": "r", "task_kind": task_kind}

    with pytest.raises(DomainError) as refused:
        contact_center_from_json(value, DESCRIPTORS)

    assert refused.value.field == "contact_center.task_kind"


def test_an_import_of_a_kind_the_platform_does_not_carry_is_refused() -> None:
    value = {"kind": "webex_cc", "routing_value": "r", "task_kind": "conversation"}

    with pytest.raises(DomainError) as refused:
        contact_center_from_json(value, {"webex_cc": BARE})

    assert refused.value.field == "contact_center.task_kind"
