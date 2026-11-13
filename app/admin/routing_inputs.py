"""The group settings form's contact-center inputs, rendered from the descriptor (ADR 0032 CC-4c).

The inputs, in form order, their labels and hints, and the link from a routing refusal
(``contact_center.<attribute>``) to its input all come from the platform's
``ProviderDescriptor``: the group form names no attribute itself, and has no ``if kind``. The
input names keep their ``wxcc_`` prefix, which the settings forms and their tests already use.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from app.admin.forms import Field, FormData
    from app.i18n import BoundGettext

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


def with_task_kind_field(
    fields: Sequence["Field"],
    form: "FormData",
    current: ContactCenterRouting | None,
    *,
    embedded_call_enabled: bool,
    _: "BoundGettext",
) -> list["Field"]:
    """Return ``fields`` with the task kind's select after the routing value's input.

    The select offers ``task_kinds_offered``; its value is the submitted one or the group's.
    A group that holds ``conversation`` while the switch is off is told, next to the select,
    that it makes work items until the switch is on (``task_kind_in_force``).
    """
    from app.admin.forms import Field, Option  # noqa: PLC0415 - forms imports this module

    kinds = task_kinds_offered(current, embedded_call_enabled=embedded_call_enabled)
    kept = current.task_kind if current else TaskKind.FORM
    hint = _("admin.field.contact_center_task_kind_hint")
    if kept is TaskKind.CONVERSATION and not embedded_call_enabled:
        hint = f"{hint} {_('admin.field.contact_center_task_kind_off')}"
    select = Field(
        TASK_KIND_FIELD,
        _(ROUTING_LABELS[TASK_KIND_FIELD]),
        "select",
        form.values.get(TASK_KIND_FIELD, kept.value),
        hint=hint,
        error=form.error_for(TASK_KIND_FIELD),
        options=tuple(Option(kind.value, _(TASK_KIND_LABELS[kind])) for kind in kinds),
    )
    after = next((n for n, f in enumerate(fields) if f.name == ROUTING_VALUE_FIELD), -1) + 1
    return [*fields[:after], select, *fields[after:]]


def routing_with_task_kind(
    center: ContactCenterRouting | None,
    form: "FormData",
    current: ContactCenterRouting | None,
    *,
    embedded_call_enabled: bool,
) -> ContactCenterRouting | None:
    """Return ``center`` made of the kind the form chose; a refused kind is a field error.

    A form without the select (an older page) keeps the group's kind. A kind that is not
    offered (``task_kinds_offered``) is refused on the select, the form kind taken so that the
    rest of the form is still checked. No routing stays none: a kind alone is no routing.

    Raises:
        DomainError: ``invalid_configuration`` naming ``contact_center.task_kind`` for a kind
            the platform does not carry.
    """
    if center is None:
        return None
    kept = current.task_kind if current else TaskKind.FORM
    if TASK_KIND_FIELD not in form.values:
        return replace(center, task_kind=kept)
    offered = task_kinds_offered(current, embedded_call_enabled=embedded_call_enabled)
    chosen = form.choice(TASK_KIND_FIELD, {kind.value for kind in offered})
    return replace(center, task_kind=TaskKind(chosen) if chosen else TaskKind.FORM)
