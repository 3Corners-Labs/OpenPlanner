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

The descriptor also declares what the platform can carry (``ContactCenterCapabilities``, ADR
0121 step 6): which interaction kinds of a flow, whether it routes on skills and whether it can
hold a conversation (ADR 0123 step 7a). A flow's readiness and the building of a task read that
declaration through ``capabilities_of`` and ``uncarried``; a descriptor that declares nothing
carries nothing.

A routing also names the kind of task a request becomes (``TaskKind``, ADR 0123 point 8): the
form (a work item, the default) or the conversation (a messaging task staff answer in their
agent desktop). The kind is stored with the routing and, once a task exists, with the task.
"""

import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from app.domain.errors import DomainError, ErrorCode
from app.domain.interaction import InteractionKind
from app.domain.networks import NetworkList, host_is_public
from app.domain.source_networks import source_networks_of

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


class TaskKind(StrEnum):
    """What a contact-center task is to the agent (ADR 0123 point 8), whatever the platform.

    A closed list: a third kind changes the ADR.
    """

    FORM = "form"
    """The work item of ADR 0018: the agent sees its fields and a link. The default."""
    CONVERSATION = "conversation"
    """A messaging task: the agent answers the participant in a conversation."""


@dataclass(frozen=True)
class ContactCenterTaskRef:
    """A booking's task at a contact-center platform (ADR 0032 point 6).

    Attributes:
        id: The platform's task id; a provider link, purged with the contact.
        kind: The platform that holds the task, and so the dispatcher that ends it.
        task_kind: The kind the task was created with, so a later step knows a conversation
            without asking the platform; later changes of the routing do not touch it.
    """

    id: str
    kind: ContactCenterKind
    task_kind: TaskKind = TaskKind.FORM


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
class ContactCenterCapabilities:
    """What one contact-center platform's adapter can carry (ADR 0121 step 6).

    A property of the adapter's code, declared as a constant in its descriptor, never a
    setting. The default carries nothing, so an adapter that forgets to declare receives no
    task and every flow on it is not ready.

    Attributes:
        interaction_kinds: The interaction kinds of a flow the platform can hand to an agent.
        skills: Whether the platform can route on the mapped skill codes (ADR 0121 step 4b).
        task_kinds: The kinds of task the platform can create (ADR 0123 step 7a): the form (a
            work item), the conversation, or both.
    """

    interaction_kinds: frozenset[InteractionKind] = frozenset()
    skills: bool = False
    task_kinds: frozenset[TaskKind] = frozenset()

    def carries(self, interaction: InteractionKind) -> bool:
        """Return whether the platform can hand work of ``interaction`` to an agent."""
        return interaction in self.interaction_kinds

    def carries_task(self, task_kind: TaskKind) -> bool:
        """Return whether the platform can create a task of ``task_kind``."""
        return task_kind in self.task_kinds


def task_kind_in_force(kind: TaskKind, conversation_enabled: bool) -> TaskKind:
    """Return the kind of task a routing makes now (ADR 0123 step 7a).

    A routing that holds ``conversation`` makes one only while the platform switch
    ``embedded_call_enabled`` is on (``conversation_enabled``), because only then can anything
    show a chat to the participant; while it is off the routing makes a form, as before the
    step. Pure: the caller passes the switch in from the edge.
    """
    if kind is TaskKind.CONVERSATION and not conversation_enabled:
        return TaskKind.FORM
    return kind


INTERACTION_KIND = "interaction_kind"
"""What ``uncarried`` names for an interaction kind the platform does not carry."""
SKILL_CODES = "skill_codes"
"""What ``uncarried`` names for skills on a platform that cannot route on them."""
TASK_KIND = "task_kind"
"""What ``uncarried`` names for a conversation task on a platform that cannot hold one, and the
field a refusal of the routing's task kind names (``contact_center.task_kind``)."""


def uncarried(
    capabilities: ContactCenterCapabilities,
    interaction: InteractionKind,
    skills: bool,
    task_kind: TaskKind = TaskKind.FORM,
) -> str | None:
    """Return what of a task the platform cannot carry, or ``None`` when it carries all of it.

    ``interaction`` is the task's interaction kind, ``skills`` whether it requires any skill
    and ``task_kind`` what kind of task it is. The interaction kind is judged first: a
    platform that cannot take the work at all is the bigger gap.
    """
    if not capabilities.carries(interaction):
        return INTERACTION_KIND
    if skills and not capabilities.skills:
        return SKILL_CODES
    if not capabilities.carries_task(task_kind):
        return TASK_KIND
    return None


@dataclass(frozen=True)
class ProviderDescriptor:
    """What one contact-center platform needs per group (ADR 0032 point 4).

    Attributes:
        kind: The platform.
        label_key: i18n key of the platform's name, for the admin form.
        attributes: The routing attributes, in form order.
        routing_value_max_length: Longest routing value the platform accepts.
        routing_value_label_key: i18n key of the routing value's label in the group form.
        routing_value_hint_key: i18n key of its hint, if it has one.
        capabilities: What the platform can carry (ADR 0121 step 6); nothing by default.
    """

    kind: ContactCenterKind
    label_key: str
    attributes: tuple[AttributeSpec, ...]
    routing_value_max_length: int
    routing_value_label_key: str
    routing_value_hint_key: str | None = None
    capabilities: ContactCenterCapabilities = ContactCenterCapabilities()

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
        task_kind: The kind of task a request becomes (ADR 0123 point 8); the form by default.

    Raises:
        DomainError: ``invalid_configuration`` naming ``contact_center.<attribute>``,
            ``contact_center.routing_value``, ``contact_center.attributes`` (a name the
            descriptor does not know) or ``contact_center.task_kind`` (a kind the platform
            does not carry).
    """

    descriptor: ProviderDescriptor = field(repr=False)
    routing_value: str | None = None
    attributes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    task_kind: TaskKind = TaskKind.FORM

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
        if not self.descriptor.capabilities.carries_task(self.task_kind):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, routing_field(TASK_KIND))

    def __hash__(self) -> int:
        """Hash by kind, routing value, attributes and task kind (a mapping is not hashable)."""
        return hash((self.kind, self.routing_value, tuple(self.attributes.items()), self.task_kind))

    def __reduce__(self) -> tuple[type["ContactCenterRouting"], tuple[object, ...]]:
        """Copy and pickle through the constructor (a mapping proxy cannot be pickled)."""
        return (
            type(self),
            (self.descriptor, self.routing_value, dict(self.attributes), self.task_kind),
        )

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


def capabilities_of(routing: ContactCenterRouting | None) -> ContactCenterCapabilities | None:
    """Return what the platform of a *complete* ``routing`` carries; ``None`` without one.

    An incomplete routing does not dispatch (ADR 0032), so it carries nothing a flow could use.
    """
    if routing is None or not routing.complete:
        return None
    return routing.descriptor.capabilities


API_HOST = re.compile(r"^https://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?/?$")
"""A platform's API host: https, a host name and at most a port (ADR 0032 point 9)."""
CLIENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
"""An OAuth client id as the platforms issue them."""
SECRET_MAX_LENGTH = 4096
"""Longest client secret or refresh token accepted; OAuth tokens are far shorter."""
CONNECTION_FIELD = "contact_center_connection."
"""Prefix of the fields a refusal of the organisation's connection names."""


@dataclass(frozen=True)
class OrganisationConnection:
    """An organisation's contact-center connection as the portal shows it (ADR 0032 point 9).

    Never a secret: only whether the client secret and the refresh token are set, and when the
    webhook secret was last generated.

    Attributes:
        kind: The platform.
        api_base_url: The platform's API host for this organisation's tenant.
        client_id: The OAuth client id (not secret).
        has_client_secret: Whether a client secret is stored.
        has_refresh_token: Whether a refresh token is stored.
        webhook_secret_at: When the webhook secret was generated; ``None`` before the first.
        allowed_networks: The networks the platform's webhooks may come from (ADR 0124 point 3),
            canonical CIDR; empty: the installation's default applies; or unreadable (a stored
            list that cannot be read, never taken for an empty one). Not secret.
    """

    kind: ContactCenterKind
    api_base_url: str
    client_id: str
    has_client_secret: bool
    has_refresh_token: bool
    webhook_secret_at: datetime | None
    allowed_networks: NetworkList = NetworkList()


@dataclass(frozen=True)
class ConnectionChange:
    """What an administrator submits for the organisation's connection.

    A ``None`` secret keeps the stored one (write-only fields render empty); so does ``None``
    allowed networks. The networks are given as the text typed, one per line or comma separated
    (``allowed_networks_text``, an ``InitVar`` as the route has the text): ``__post_init__`` parses
    it once, with the rule of ``app.domain.source_networks``, into ``allowed_networks``, the tuple
    that everything after it uses. An empty text clears the list, so the installation's default
    applies again. A tuple given directly is checked by the same rule.

    Raises:
        DomainError: ``invalid_configuration`` naming the refused field.
    """

    kind: ContactCenterKind
    api_base_url: str
    client_id: str
    client_secret: str | None = None
    refresh_token: str | None = None
    allowed_networks: tuple[str, ...] | None = None
    allowed_networks_text: InitVar[str | None] = None

    def __post_init__(self, allowed_networks_text: str | None) -> None:
        """Validate every field and drop a trailing slash; secrets are checked for shape only."""
        if not API_HOST.fullmatch(self.api_base_url) or not host_is_public(self.api_base_url):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, f"{CONNECTION_FIELD}api_base_url")
        object.__setattr__(self, "api_base_url", self.api_base_url.rstrip("/"))
        if not CLIENT_ID.fullmatch(self.client_id):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, f"{CONNECTION_FIELD}client_id")
        if self.client_secret is not None and not _secret_shape(self.client_secret):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, f"{CONNECTION_FIELD}client_secret")
        if self.refresh_token is not None and not _secret_shape(self.refresh_token):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, f"{CONNECTION_FIELD}refresh_token")
        typed = allowed_networks_text
        if typed is None and self.allowed_networks is not None:
            typed = "\n".join(self.allowed_networks)
        if typed is not None:
            networks = source_networks_of(typed, field=f"{CONNECTION_FIELD}allowed_networks")
            object.__setattr__(self, "allowed_networks", networks)

    @property
    def secrets_sent(self) -> tuple[str, ...]:
        """Return the names of the secrets this change replaces (for the audit, never values)."""
        sent = (("client_secret", self.client_secret), ("refresh_token", self.refresh_token))
        return tuple(name for name, value in sent if value)


def _secret_shape(value: str) -> bool:
    """Whether a submitted secret is one printable token without spaces, within the cap."""
    return 0 < len(value) <= SECRET_MAX_LENGTH and value.isprintable() and " " not in value


class Registration(StrEnum):
    """What telling the platform about an organisation's webhook address came to (CC-4d)."""

    REGISTERED = "registered"
    """The platform signs the organisation's events with its current secret now."""
    NOT_READY = "not_ready"
    """A secret is missing; the address is registered once the connection is complete."""
    UNTOUCHED = "untouched"
    """The connection has its webhook secret and nothing the subscription depends on changed
    (only the allowed networks), so it was left as it was (ADR 0124 point 3)."""
    REFUSED = "refused"
    """The platform refused (rights, address or secret); the reason is in the request log."""
    FAILED = "failed"
    """The platform could not be reached; trying again later may work."""
