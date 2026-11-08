"""Provider-neutral contact-center routing (ADR 0032).

A contact-center platform is data: ``ContactCenterKind`` names it, and the platform's adapter
describes the routing attributes it needs in a ``ProviderDescriptor``. A group's
``ContactCenterRouting`` holds that descriptor, the routing value and the attributes; it is
validated here, generically, against the descriptor. The domain knows the kinds, never their
APIs or attribute names.

    adapter ──► ProviderDescriptor(kind, AttributeSpec(name, max_length, required, keys) …)
                         │
    group ──► ContactCenterRouting(descriptor, routing_value, attributes)
                         │  validated at construction (lengths, known names, plain text)
                         ▼
               complete? ── every required attribute present ──► the group dispatches

A routing may be incomplete (an admin filled in one attribute of two): it is stored as it is
and the group does not dispatch until it is complete, as before ADR 0032.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from app.domain.errors import DomainError, ErrorCode

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_ATTRIBUTE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

FIELD_PREFIX = "contact_center."
"""Prefix of the field a refusal names: ``contact_center.<attribute>``."""
ROUTING_VALUE = "routing_value"
"""The field name of the routing value in a refusal (``contact_center.routing_value``)."""
ATTRIBUTES = "attributes"
"""The field a refusal names for an attribute the descriptor does not know."""


class ContactCenterKind(StrEnum):
    """A contact-center platform OpenPlanner can dispatch to (ADR 0032 point 1)."""

    WEBEX_CC = "webex_cc"
    """Webex Contact Center, through its Tasks API (ADR 0018)."""


@dataclass(frozen=True)
class ContactCenterTaskRef:
    """A booking's task at a contact-center platform (ADR 0032 point 6).

    Attributes:
        id: The platform's task id; a provider link, purged with the contact.
        kind: The platform that holds the task, and so the dispatcher that ends it.
    """

    id: str
    kind: ContactCenterKind


def routing_field(name: str) -> str:
    """Return the field a refusal of routing attribute (or routing value) ``name`` names."""
    return f"{FIELD_PREFIX}{name}"


@dataclass(frozen=True)
class AttributeSpec:
    """One routing attribute a provider needs, as its adapter describes it.

    Attributes:
        name: Stable key in ``ContactCenterRouting.attributes`` (lowercase, ``_``).
        max_length: Longest value the platform accepts.
        required: Whether the group dispatches only with this attribute filled in.
        label_key: i18n key of the attribute's label in the admin form.
        hint_key: i18n key of its hint, if it has one.
    """

    name: str
    max_length: int
    required: bool
    label_key: str
    hint_key: str | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed specification (a programming error, not user input).

        Raises:
            ValueError: For a name that is not a lowercase key or a length below 1.
        """
        if not _ATTRIBUTE_NAME.fullmatch(self.name) or self.max_length < 1:
            raise ValueError("invalid attribute spec")


@dataclass(frozen=True)
class ProviderDescriptor:
    """What one contact-center platform needs per group (ADR 0032 point 4).

    Attributes:
        kind: The platform.
        label_key: i18n key of the platform's name, for the admin form.
        attributes: The routing attributes, in form order.
        routing_value_max_length: Longest routing value the platform accepts.
    """

    kind: ContactCenterKind
    label_key: str
    attributes: tuple[AttributeSpec, ...]
    routing_value_max_length: int

    def __post_init__(self) -> None:
        """Refuse duplicate attribute names or a routing value limit below 1.

        Raises:
            ValueError: For a malformed descriptor (a programming error).
        """
        names = [spec.name for spec in self.attributes]
        if len(names) != len(set(names)) or ROUTING_VALUE in names:
            raise ValueError("duplicate attribute name")
        if self.routing_value_max_length < 1:
            raise ValueError("invalid routing value limit")

    def spec(self, name: str) -> AttributeSpec | None:
        """Return the specification of attribute ``name``, or ``None`` if there is none."""
        return next((spec for spec in self.attributes if spec.name == name), None)

    @property
    def required(self) -> frozenset[str]:
        """The names of the attributes a group needs before it dispatches."""
        return frozenset(spec.name for spec in self.attributes if spec.required)


def _check_text(value: str, limit: int, name: str) -> None:
    """Refuse a blank, too long or control-character ``value`` of field ``name``.

    Raises:
        DomainError: ``invalid_configuration`` with field ``contact_center.<name>``.
    """
    if not value.strip() or len(value) > limit or _CONTROL_CHARS.search(value):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, routing_field(name))


@dataclass(frozen=True)
class ContactCenterRouting:
    """A group's routing at its contact-center platform (ADR 0032 point 3).

    Attributes:
        descriptor: The platform's descriptor; gives the kind and the rules.
        routing_value: What the platform's flow routes on; the group slug when ``None``.
        attributes: The platform's attributes by name, read-only; absent means not set.

    Raises:
        DomainError: ``invalid_configuration`` naming ``contact_center.<attribute>``,
            ``contact_center.routing_value`` or ``contact_center.attributes`` (a name the
            descriptor does not know).
    """

    descriptor: ProviderDescriptor = field(repr=False)
    routing_value: str | None = None
    attributes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Freeze the attributes and validate everything against the descriptor."""
        frozen = MappingProxyType(dict(sorted(self.attributes.items())))
        object.__setattr__(self, "attributes", frozen)
        if self.routing_value is not None:
            _check_text(self.routing_value, self.descriptor.routing_value_max_length, ROUTING_VALUE)
        for name, value in frozen.items():
            spec = self.descriptor.spec(name)
            if spec is None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, routing_field(ATTRIBUTES))
            _check_text(value, spec.max_length, name)

    def __hash__(self) -> int:
        """Hash by kind, routing value and attributes (a mapping is not hashable itself)."""
        return hash((self.kind, self.routing_value, tuple(self.attributes.items())))

    def __reduce__(self) -> tuple[type["ContactCenterRouting"], tuple[object, ...]]:
        """Copy and pickle through the constructor (a mapping proxy cannot be pickled)."""
        return (type(self), (self.descriptor, self.routing_value, dict(self.attributes)))

    @property
    def kind(self) -> ContactCenterKind:
        """The platform this routing is for."""
        return self.descriptor.kind

    @property
    def complete(self) -> bool:
        """Whether every attribute the platform requires is filled in."""
        return self.descriptor.required <= self.attributes.keys()

    def value_for(self, slug: str) -> str:
        """Return the value the platform routes on: the configured one, or ``slug``."""
        return self.routing_value or slug

    def attribute(self, name: str) -> str | None:
        """Return attribute ``name``, or ``None`` when it is not set."""
        return self.attributes.get(name)
