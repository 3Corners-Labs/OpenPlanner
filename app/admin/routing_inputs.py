"""The group settings form's contact-center inputs, rendered from the descriptor (ADR 0032 CC-4c).

The inputs, in form order, their labels and hints, and the link from a routing refusal
(``contact_center.<attribute>``) to its input all come from the platform's
``ProviderDescriptor``: the group form names no attribute itself, and has no ``if kind``. The
input names keep their ``wxcc_`` prefix, which the settings forms and their tests already use.
"""

from collections.abc import Mapping

from app.adapters.contact_centers import DESCRIPTORS
from app.domain.contact_center import (
    ATTRIBUTES,
    ROUTING_VALUE,
    ContactCenterKind,
    routing_field,
)

FORM_KIND = ContactCenterKind.WEBEX_CC
"""The platform a group's routing is for: the only one with an adapter (a second one picks the
organisation's connection kind here, CC-6)."""
_DESCRIPTOR = DESCRIPTORS[FORM_KIND]

_ATTRIBUTE_INPUTS = tuple((f"wxcc_{spec.name}", spec) for spec in _DESCRIPTOR.attributes)
"""Each attribute's input name with its specification: the one place the input names are made."""

ROUTING_ATTRIBUTE_FIELDS: Mapping[str, str] = {name: spec.name for name, spec in _ATTRIBUTE_INPUTS}
"""Input name → routing attribute it fills."""
ROUTING_VALUE_FIELD = "wxcc_routing_value"
"""The input of the routing value."""
ROUTING_LABELS: Mapping[str, str] = {
    **{name: spec.label_key for name, spec in _ATTRIBUTE_INPUTS},
    ROUTING_VALUE_FIELD: _DESCRIPTOR.routing_value_label_key,
}
"""Input name → i18n key of its label."""
ROUTING_HINTS: Mapping[str, str | None] = {
    **{name: spec.hint_key for name, spec in _ATTRIBUTE_INPUTS},
    ROUTING_VALUE_FIELD: _DESCRIPTOR.routing_value_hint_key,
}
"""Input name → i18n key of its hint, if any."""
ROUTING_FIELDS: tuple[str, ...] = (*ROUTING_ATTRIBUTE_FIELDS, ROUTING_VALUE_FIELD)
"""The routing inputs in form order: the descriptor's attributes, then the routing value."""
ROUTING_TARGETS: Mapping[str, str] = {
    **{routing_field(name): field for field, name in ROUTING_ATTRIBUTE_FIELDS.items()},
    routing_field(ROUTING_VALUE): ROUTING_VALUE_FIELD,
    routing_field(ATTRIBUTES): next(iter(ROUTING_ATTRIBUTE_FIELDS)),
}
"""Field a routing refusal names → the input it is linked to."""
ROUTING_MESSAGE = "admin.form.error.integration_text"
"""The message of every routing refusal (blank, too long or a control character)."""
