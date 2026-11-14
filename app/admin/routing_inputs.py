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
    TASK_KIND,
    ContactCenterKind,
    ContactCenterRouting,
    TaskKind,
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
TASK_KIND_FIELD = "contact_center_task_kind"
"""The select of the kind of task a request becomes (ADR 0123 point 8), after the text inputs."""
ROUTING_LABELS: Mapping[str, str] = {
    **{name: spec.label_key for name, spec in _ATTRIBUTE_INPUTS},
    ROUTING_VALUE_FIELD: _DESCRIPTOR.routing_value_label_key,
    TASK_KIND_FIELD: "admin.field.contact_center_task_kind",
}
"""Input name → i18n key of its label."""
ROUTING_HINTS: Mapping[str, str | None] = {
    **{name: spec.hint_key for name, spec in _ATTRIBUTE_INPUTS},
    ROUTING_VALUE_FIELD: _DESCRIPTOR.routing_value_hint_key,
}
"""Input name → i18n key of its hint, if any."""
ROUTING_FIELDS: tuple[str, ...] = (*ROUTING_ATTRIBUTE_FIELDS, ROUTING_VALUE_FIELD)
"""The routing text inputs in form order: the descriptor's attributes, then the routing value."""
TASK_KIND_LABELS: Mapping[TaskKind, str] = {
    TaskKind.FORM: "admin.contact_center.task_kind.form",
    TaskKind.CONVERSATION: "admin.contact_center.task_kind.conversation",
}
"""Task kind → i18n key of its option label."""
TASK_KIND_MESSAGE = "admin.form.error.task_kind"
"""The message of a refused task kind."""
ROUTING_TARGETS: Mapping[str, str] = {
    **{routing_field(name): field for field, name in ROUTING_ATTRIBUTE_FIELDS.items()},
    routing_field(ROUTING_VALUE): ROUTING_VALUE_FIELD,
    routing_field(ATTRIBUTES): next(iter(ROUTING_ATTRIBUTE_FIELDS)),
    routing_field(TASK_KIND): TASK_KIND_FIELD,
}
"""Field a routing refusal names → the input it is linked to."""
ROUTING_MESSAGE = "admin.form.error.integration_text"
"""The message of every routing refusal (blank, too long or a control character)."""


def task_kinds_offered(
    current: ContactCenterRouting | None, *, embedded_call_enabled: bool
) -> tuple[TaskKind, ...]:
    """Return the task kinds the group form offers and accepts (ADR 0123 step 7a).

    The form is always offered. The conversation is offered only while the platform switch
    ``embedded_call_enabled`` is on (until then nothing can show a chat to a participant) and
    only when the platform declares that it carries conversations. A group that holds
    ``conversation`` already keeps it on the list, so that saving another setting never resets
    it unseen; choosing it afresh stays closed while the switch is off.
    """
    carried = _DESCRIPTOR.capabilities.carries_task(TaskKind.CONVERSATION)
    holds = current is not None and current.task_kind is TaskKind.CONVERSATION
    if carried and (embedded_call_enabled or holds):
        return (TaskKind.FORM, TaskKind.CONVERSATION)
    return (TaskKind.FORM,)
