"""The registry of contact-center platforms: one descriptor per kind (ADR 0032 point 4).

Each platform adapter defines its ``ProviderDescriptor``; this module is the one list of them,
used wherever a group's ``ContactCenterRouting`` is built (the database adapter, the admin form),
and of their inbound webhooks (``INBOUND``, read by ``/webhooks/contact-center/{kind}``).
Adding a platform adds its descriptor here; domain, jobs and templates stay as they are.

The attributes are stored as one JSON object of strings (``group_contact_center.attributes``);
``attributes_json`` and ``attributes_from_json`` are the two directions.
"""

import json
from collections.abc import Mapping

from app.adapters.webhooks import WXCC_INBOUND, ContactCenterWebhook
from app.adapters.wxcc import DESCRIPTOR as WEBEX_CC
from app.domain.contact_center import (
    ContactCenterKind,
    ContactCenterRouting,
    ProviderDescriptor,
    TaskKind,
)

DESCRIPTORS: Mapping[ContactCenterKind, ProviderDescriptor] = {WEBEX_CC.kind: WEBEX_CC}
"""Every platform OpenPlanner has an adapter for, by kind."""

INBOUND: Mapping[ContactCenterKind, ContactCenterWebhook] = {WXCC_INBOUND.kind: WXCC_INBOUND}
"""How each platform's webhooks are checked and read (ADR 0032 point 6), by kind."""


def inbound(kind: str) -> ContactCenterWebhook | None:
    """Return the webhook handling of the platform named ``kind``; ``None`` for any other text.

    ``kind`` comes from the URL, so an unknown or malformed value is an answer, not an error.
    """
    try:
        return INBOUND.get(ContactCenterKind(kind))
    except ValueError:
        return None


def descriptor(kind: ContactCenterKind) -> ProviderDescriptor:
    """Return the descriptor of ``kind``.

    Raises:
        KeyError: For a kind without an adapter (a programming error).
    """
    return DESCRIPTORS[kind]


def routing(
    kind: ContactCenterKind,
    routing_value: str | None,
    attributes: Mapping[str, str | None],
    task_kind: TaskKind = TaskKind.FORM,
) -> ContactCenterRouting | None:
    """Return the routing of ``kind`` with the given values, or ``None`` when all are empty.

    An attribute whose value is ``None`` is left out (not set). A task kind alone is no
    routing: with no routing value and no attribute there is nothing to dispatch to, and the
    kind is not kept.

    Raises:
        DomainError: ``invalid_configuration`` for a value the descriptor refuses.
    """
    filled = {name: value for name, value in attributes.items() if value is not None}
    if routing_value is None and not filled:
        return None
    return ContactCenterRouting(descriptor(kind), routing_value, filled, task_kind)


def attributes_json(attributes: Mapping[str, str]) -> str:
    """Return the attributes as the stored JSON object, keys sorted."""
    return json.dumps(dict(attributes), sort_keys=True, separators=(",", ":"))


def attributes_from_json(text: str) -> dict[str, str]:
    """Return the stored attributes, an object of strings.

    Raises:
        ValueError: For text that is not JSON or not an object of strings (a broken row is
            reported, not read as a group without routing).
    """
    value = json.loads(text)
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ValueError("contact-center attributes must be an object of strings")
    return dict(value)
