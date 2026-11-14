"""A group's contact-center routing as a JSON object (ADR 0032, ADR 0099).

The configuration document holds the routing of a group as ``{"kind": ..., "routing_value":
..., "attributes": {...}}``, whatever the platform, so adding a platform changes no document
format. A routing that makes conversation tasks (ADR 0123 step 7a) adds ``"task_kind":
"conversation"``; the form, the default, is left out, so a document of a routing that makes
work items is what it always was and an import without the key makes work items. The
platform's descriptor (what it needs, and how long each value may be) comes in as an argument:
the domain knows the kinds, never their adapters.
"""

from collections.abc import Mapping

from app.domain.contact_center import (
    TASK_KIND,
    ContactCenterKind,
    ContactCenterRouting,
    ProviderDescriptor,
    TaskKind,
    routing_field,
)
from app.domain.errors import DomainError, ErrorCode

_FIELD = "contact_center"
_KEYS = frozenset({"kind", "routing_value", "attributes", "task_kind"})


def _invalid(part: str = "") -> DomainError:
    """Return the refusal that names the routing field, or one part of it."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, f"{_FIELD}.{part}" if part else _FIELD)


def contact_center_from_json(
    value: object, descriptors: Mapping[str, ProviderDescriptor]
) -> ContactCenterRouting | None:
    """Return the routing a document field describes; ``None`` for no routing.

    Args:
        value: The JSON value of the field.
        descriptors: The descriptor of every platform the installation has an adapter for, by
            the value of its ``ContactCenterKind``.

    Raises:
        DomainError: ``invalid_configuration`` naming ``contact_center`` or a part of it, for
            a shape that is not ours, an unknown platform, or a value the descriptor refuses.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or not set(value) <= _KEYS:
        raise _invalid()
    kind = value.get("kind")
    descriptor = descriptors.get(kind) if isinstance(kind, str) else None
    if descriptor is None:
        raise _invalid("kind")
    routing_value = value.get("routing_value")
    if routing_value is not None and not isinstance(routing_value, str):
        raise _invalid("routing_value")
    attributes = value.get("attributes", {})
    if not isinstance(attributes, Mapping) or not all(
        isinstance(name, str) and isinstance(item, str) for name, item in attributes.items()
    ):
        raise _invalid("attributes")
    task_kind = value.get("task_kind", TaskKind.FORM.value)
    if not isinstance(task_kind, str) or task_kind not in {m.value for m in TaskKind}:
        raise _invalid("task_kind")
    try:
        routing = ContactCenterRouting(
            descriptor, routing_value, dict(attributes), TaskKind(task_kind)
        )
    except DomainError as error:
        if error.field == routing_field(TASK_KIND):  # a kind this platform does not carry
            raise _invalid("task_kind") from error
        raise
    return routing if routing.routing_value is not None or routing.attributes else None


def contact_center_to_json(routing: ContactCenterRouting | None) -> dict[str, object] | None:
    """Return the routing as the JSON object a document holds; ``None`` for no routing."""
    if routing is None:
        return None
    document: dict[str, object] = {
        "kind": ContactCenterKind(routing.kind).value,
        "routing_value": routing.routing_value,
        "attributes": dict(routing.attributes),
    }
    if routing.task_kind is not TaskKind.FORM:
        document["task_kind"] = routing.task_kind.value
    return document
