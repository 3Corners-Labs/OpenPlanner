"""Provider-neutral routing validated against a provider's descriptor (ADR 0032 point 4)."""

import copy
import pickle

import pytest

from app.domain.contact_center import (
    AttributeSpec,
    ContactCenterKind,
    ContactCenterRouting,
    ProviderDescriptor,
    routing_field,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import NEXT_JOB_TYPES, JobType
from tests.support.domain import make_group

DESCRIPTOR = ProviderDescriptor(
    kind=ContactCenterKind.WEBEX_CC,
    label_key="admin.contact_center.webex_cc",
    attributes=(
        AttributeSpec("queue", 10, True, "label.queue"),
        AttributeSpec("skill", 5, False, "label.skill", "hint.skill"),
    ),
    routing_value_max_length=8,
)


def test_a_routing_holds_its_values_read_only() -> None:
    routing = ContactCenterRouting(DESCRIPTOR, "team-1", {"skill": "nl", "queue": "q"})

    assert routing.kind is ContactCenterKind.WEBEX_CC
    assert list(routing.attributes) == ["queue", "skill"]
    assert routing.attribute("queue") == "q"
    assert routing.attribute("other") is None
    assert routing.value_for("slug") == "team-1"
    with pytest.raises(TypeError):
        routing.attributes["queue"] = "x"  # type: ignore[index]


def test_the_slug_is_the_routing_value_when_none_is_set() -> None:
    assert ContactCenterRouting(DESCRIPTOR).value_for("my-group") == "my-group"


def test_complete_needs_every_required_attribute() -> None:
    assert ContactCenterRouting(DESCRIPTOR, attributes={"queue": "q"}).complete
    assert not ContactCenterRouting(DESCRIPTOR, attributes={"skill": "nl"}).complete
    assert not ContactCenterRouting(DESCRIPTOR, "team").complete


@pytest.mark.parametrize(
    ("routing_value", "attributes", "field"),
    [
        ("123456789", {}, "routing_value"),
        ("   ", {}, "routing_value"),
        ("a\nb", {}, "routing_value"),
        (None, {"queue": ""}, "queue"),
        (None, {"queue": "q" * 11}, "queue"),
        (None, {"skill": "s\tk"}, "skill"),
        (None, {"unknown": "x"}, "attributes"),
    ],
)
def test_values_the_descriptor_refuses_name_their_field(
    routing_value: str | None, attributes: dict[str, str], field: str
) -> None:
    with pytest.raises(DomainError) as excinfo:
        ContactCenterRouting(DESCRIPTOR, routing_value, attributes)

    assert excinfo.value.code is ErrorCode.INVALID_CONFIGURATION
    assert excinfo.value.field == routing_field(field)


def test_routings_compare_hash_copy_and_pickle_by_value() -> None:
    one = ContactCenterRouting(DESCRIPTOR, "r", {"queue": "q"})
    two = ContactCenterRouting(DESCRIPTOR, "r", {"queue": "q"})

    assert one == two
    assert hash(one) == hash(two)
    assert one != ContactCenterRouting(DESCRIPTOR, "r", {"queue": "other"})
    assert copy.deepcopy(one) == one
    assert pickle.loads(pickle.dumps(one)) == one  # noqa: S301 - our own object


@pytest.mark.parametrize(
    "attributes",
    [
        (AttributeSpec("a", 1, True, "k"), AttributeSpec("a", 1, False, "k")),
        (AttributeSpec("routing_value", 1, True, "k"),),
    ],
)
def test_a_descriptor_with_clashing_names_is_a_programming_error(
    attributes: tuple[AttributeSpec, ...],
) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ProviderDescriptor(ContactCenterKind.WEBEX_CC, "k", attributes, 10)


def test_malformed_specs_are_programming_errors() -> None:
    with pytest.raises(ValueError, match="attribute spec"):
        AttributeSpec("Not A Key", 10, True, "k")
    with pytest.raises(ValueError, match="attribute spec"):
        AttributeSpec("key", 0, True, "k")
    with pytest.raises(ValueError, match="routing value"):
        ProviderDescriptor(ContactCenterKind.WEBEX_CC, "k", (), 0)


def test_a_group_dispatches_only_with_a_complete_routing() -> None:
    complete = ContactCenterRouting(DESCRIPTOR, attributes={"queue": "q"})
    incomplete = ContactCenterRouting(DESCRIPTOR, attributes={"skill": "nl"})

    assert make_group(contact_center=complete).dispatch_enabled
    assert not make_group(contact_center=incomplete).dispatch_enabled
    assert not make_group().dispatch_enabled
    assert make_group(contact_center=complete).routing_value == "aangifte-2026"


# --- the job type renamed by expand/contract (ADR 0116) -----------------------------------


def test_the_renamed_dispatch_job_keeps_its_stored_value_this_minor() -> None:
    # ADR 0116 §5: the previous image must read every job this image writes.
    assert JobType.DISPATCH_CONTACT_CENTER_TASK.value == "dispatch_wxcc_task"
    assert JobType("dispatch_wxcc_task") is JobType.DISPATCH_CONTACT_CENTER_TASK
    # The value the next minor writes already reads, for a rollback.
    assert JobType("dispatch_contact_center_task") is JobType.DISPATCH_CONTACT_CENTER_TASK
    assert "dispatch_contact_center_task" not in {t.value for t in JobType}
    with pytest.raises(ValueError, match="no_such_job"):
        JobType("no_such_job")


def test_every_next_value_reads_as_an_existing_member() -> None:
    assert set(NEXT_JOB_TYPES.values()) <= set(JobType)
    assert not set(NEXT_JOB_TYPES) & {t.value for t in JobType}
