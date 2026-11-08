"""The group settings form's contact-center inputs, derived from the WxCC descriptor (ADR 0032).

Until CC-4 renders the form from the platform's descriptor, the form keeps its WxCC inputs
(``wxcc_<attribute>`` and ``wxcc_routing_value``). Their names, labels and the link from a
routing refusal (``contact_center.<attribute>``) to the input all come from the descriptor
here, so nothing about the attributes is written down twice.
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
"""The platform the settings form edits until CC-4."""
_DESCRIPTOR = DESCRIPTORS[FORM_KIND]

ROUTING_ATTRIBUTE_FIELDS: Mapping[str, str] = {
    f"wxcc_{spec.name}": spec.name for spec in _DESCRIPTOR.attributes
}
"""Input name → routing attribute it fills."""
ROUTING_VALUE_FIELD = "wxcc_routing_value"
"""The input of the routing value."""
ROUTING_LABELS: Mapping[str, str] = {
    **{f"wxcc_{spec.name}": spec.label_key for spec in _DESCRIPTOR.attributes},
    ROUTING_VALUE_FIELD: "admin.field.wxcc_routing_value",
}
"""Input name → i18n key of its label."""
ROUTING_TARGETS: Mapping[str, str] = {
    **{routing_field(name): field for field, name in ROUTING_ATTRIBUTE_FIELDS.items()},
    routing_field(ROUTING_VALUE): ROUTING_VALUE_FIELD,
    routing_field(ATTRIBUTES): next(iter(ROUTING_ATTRIBUTE_FIELDS)),
}
"""Field a routing refusal names → the input it is linked to."""
ROUTING_MESSAGE = "admin.form.error.integration_text"
"""The message of every routing refusal (blank, too long or a control character)."""
