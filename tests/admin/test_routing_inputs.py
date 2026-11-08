"""The group form's contact-center inputs come from the platform's descriptor (ADR 0032 CC-4c)."""

from app.adapters.contact_centers import DESCRIPTORS
from app.admin.routing_inputs import (
    FORM_KIND,
    ROUTING_FIELDS,
    ROUTING_HINTS,
    ROUTING_LABELS,
    ROUTING_VALUE_FIELD,
)


def test_the_group_form_inputs_follow_the_descriptor() -> None:
    """The form names no attribute itself; its descriptor does, in its order."""
    descriptor = DESCRIPTORS[FORM_KIND]
    attributes = tuple(f"wxcc_{spec.name}" for spec in descriptor.attributes)

    assert ROUTING_FIELDS == (*attributes, ROUTING_VALUE_FIELD)
    assert [ROUTING_LABELS[name] for name in ROUTING_FIELDS] == [
        *(spec.label_key for spec in descriptor.attributes),
        descriptor.routing_value_label_key,
    ]
    assert ROUTING_HINTS[ROUTING_VALUE_FIELD] == descriptor.routing_value_hint_key
