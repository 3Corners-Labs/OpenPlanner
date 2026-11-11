"""What planning a configuration document produces, and the facts it is checked against.

A *change* is one object created, updated or deleted; a *refusal* is one thing that stops the
plan, with a code and the path of the offending object; a *usage* is what the installation
knows that its configuration does not (which groups bookings refer to, which settings the
environment locks). A plan lists changes by field *name* and never holds a value in its text
form: a change carries the values the adapter writes, and its ``repr`` leaves them out.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.domain.authorization import Permission, Target
from app.domain.configuration_tree import Address, Entity
from app.domain.configuration_values import Value
from app.domain.contact_center import ProviderDescriptor
from app.domain.errors import ErrorCode


class ChangeAction(StrEnum):
    """What a plan does to one object.

    Attributes:
        CREATE: The object is added.
        UPDATE: Some of its fields change.
        DELETE: It is removed, with everything it holds.
        ARCHIVE: It is kept but marked archived, because something still refers to it (a kind
            that declares ``Kind.archive``); never a deletion, and reversible by a document.
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class Refusal:
    """One thing that stops a plan.

    Attributes:
        path: Where in the document the offending object is.
        code: Why; the codes of ADR 0099 and the admin forms' own.
        field: The offending field, when the refusal is about one; never a value.
    """

    path: str
    code: ErrorCode
    field: str | None = None


@dataclass(frozen=True)
class Change:
    """One object the plan creates, updates or deletes.

    Attributes:
        kind: The object's kind.
        key: Its natural key within its parent.
        address: Its natural keys from the top of the tree.
        path: Where it is in the document.
        action: What happens to it.
        fields: The names of the fields that are written (all given ones on create); a plan
            shows these and never a value.
        values: Every field as it will be, which the adapter writes; left out of ``repr``.
        permissions: What the admin screens of these fields require.
        organisation: The slug of the organisation it belongs to, if any.
        group: The slug of the group it belongs to, if any.
    """

    kind: str
    key: tuple[str, ...]
    address: Address
    path: str
    action: ChangeAction
    fields: tuple[str, ...] = ()
    values: Mapping[str, Value] = field(default_factory=dict, repr=False)
    permissions: frozenset[Permission] = frozenset()
    organisation: str | None = None
    group: str | None = None


@dataclass(frozen=True)
class RuleContext:
    """What the rules of a document need from outside it.

    Attributes:
        locales: The locales the installation has a catalogue for.
        production: Whether this is a production installation (stricter address rules).
        now: The current time (UTC), for rules about the past.
        settings: The platform's effective settings by registry key (limits such as the
            number of custom form fields), as the running installation uses them.
        built_in_claim_values: The login values of the built-in roles, which a custom role
            may not take.
        smtp_plaintext_relay_allowed: Whether the host allows an unencrypted mail relay.
        combination_problem: Names the rule that a set of settings values breaks together
            (ADR 0042), or ``None``; supplied by the layer that can read the environment.
        contact_centers: The descriptor of every contact-center platform the installation has
            an adapter for, by the value of its kind (ADR 0032).
    """

    locales: frozenset[str] = frozenset({"nl", "en"})
    production: bool = False
    now: datetime = datetime(2000, 1, 1, tzinfo=UTC)
    settings: Mapping[str, object] = field(default_factory=dict)
    built_in_claim_values: frozenset[str] = frozenset()
    smtp_plaintext_relay_allowed: bool = False
    combination_problem: Callable[[Mapping[str, object]], str | None] | None = None
    contact_centers: Mapping[str, ProviderDescriptor] = field(default_factory=dict)


@dataclass(frozen=True)
class SlotLoad:
    """A future slot of a group and the places already booked on it (ADR 0099 point 7).

    Attributes:
        start: When the slot starts (UTC).
        end: When it ends (UTC).
        booked: The places taken by bookings that hold one.
    """

    start: datetime
    end: datetime
    booked: int


@dataclass(frozen=True)
class Usage:
    """Facts about the installation that its configuration does not hold.

    Attributes:
        in_use: Per kind, the objects (by address) that something outside the configuration
            refers to: a booking, a closure or a shortfall acceptance. They are not deleted.
        kept: Per kind, the objects (by address) that a document archives instead of deleting
            when it removes them, because they hold history or grants (a skill a person holds
            or a revision requires, a pool a revision draws on, a flow with a published
            revision). Unlike ``in_use`` it does not make what holds them in use: a group is
            deleted with its flows, which are history inside it. Only a kind that declares
            ``Kind.archive`` is kept; for any other kind this is ignored.
        future_bookings: The groups (by address) with a future booking that holds a place;
            their time grid is frozen.
        slot_loads: Per group (by address), its future slots and what is booked on them.
        locked_settings: The settings the environment locks (ADR 0042), by registry key.
        unaddressable: Objects of the installation that a document cannot address and so cannot
            carry, named ``organisation/id`` (a counted capacity pool whose code is missing); an
            export refuses while there is one.
    """

    in_use: Mapping[str, frozenset[Address]] = field(default_factory=dict)
    kept: Mapping[str, frozenset[Address]] = field(default_factory=dict)
    future_bookings: frozenset[Address] = frozenset()
    slot_loads: Mapping[Address, tuple[SlotLoad, ...]] = field(default_factory=dict)
    locked_settings: frozenset[str] = frozenset()
    unaddressable: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Resolution:
    """What a document does to the installation, before the cross-object rules look at it.

    Attributes:
        current: The installation as it is now.
        resulting: The installation as it would be after the changes.
        changes: Every object written, parents first.
        deletions: Every object removed, in the order the planner found them.
        usage: The facts the rules check against.
        context: What the rules need from outside the document.
    """

    current: Entity
    resulting: Entity
    changes: tuple[Change, ...]
    deletions: tuple[Change, ...]
    usage: Usage
    context: RuleContext


type IntegrityRule = Callable[[Resolution], Iterable[Refusal]]
"""A rule across objects: it reads the resolution and yields what it refuses."""

type MayChange = Callable[[Permission, Target], bool]
"""Whether the actor holds a permission on a target, decided by the matrix (ADR 0014)."""


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """The installation's configuration as it is now, read in one transaction.

    Attributes:
        root: The configuration tree, in the shape of the document (secrets that are set are
            ``Secret`` objects; without a settings key there are none).
        usage: What the installation knows that the configuration does not.
        version: The settings version the snapshot was read at.
        secrets_available: Whether the installation can open its sealed secrets (it has a
            settings key); without it no secret is in the tree.
        settings: The portal values of the class C settings, which win over the process's own
            defaults wherever a rule reads a limit.
    """

    root: Entity
    usage: Usage
    version: int
    secrets_available: bool = True
    settings: Mapping[str, object] = field(default_factory=dict)
