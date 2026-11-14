"""The group settings form's select for the kind of contact-center task (ADR 0123 step 7a).

Next to the routing's text inputs (``app.admin.routing_inputs``, which ``app.admin.forms``
imports, so form fields cannot be built there): ``with_task_kind_field`` adds the select to the
integration inputs and ``routing_with_task_kind`` reads what the form chose into the routing. The
offered kinds and the labels are ``routing_inputs``'s; the group's route calls these two.
"""

from collections.abc import Sequence
from dataclasses import replace

from app.admin.forms import Field, FormData, Option
from app.admin.routing_inputs import (
    ROUTING_LABELS,
    ROUTING_VALUE_FIELD,
    TASK_KIND_FIELD,
    TASK_KIND_LABELS,
    task_kinds_offered,
)
from app.domain.contact_center import ContactCenterRouting, TaskKind
from app.i18n import BoundGettext


def with_task_kind_field(
    fields: Sequence[Field],
    form: FormData,
    current: ContactCenterRouting | None,
    *,
    embedded_call_enabled: bool,
    _: BoundGettext,
) -> list[Field]:
    """Return ``fields`` with the task kind's select after the routing value's input.

    The select offers ``task_kinds_offered``; its value is the submitted one or the group's.
    A group that holds ``conversation`` while the switch is off is told, next to the select,
    that it makes work items until the switch is on (``task_kind_in_force``).
    """
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
    form: FormData,
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
