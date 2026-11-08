"""The tree a configuration document and the installation's configuration are made of.

Both the document that states the desired configuration and the snapshot of what the
installation has now are trees of ``Entity`` objects, so the planner can compare them without
knowing what either was read from (ADR 0099 point 6). The tree holds no behaviour beyond
walking it (``walk``, ``map_fields``, ``any_field``, ``drop_fields``); reading it from JSON is
``configuration_document``, comparing two of them is ``configuration_plan``.
"""

from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.domain.configuration_values import Value

type Address = tuple[str, ...]
"""An object's natural keys from the top of the tree: ``("acme", "helpdesk")`` for a group."""


class Action(StrEnum):
    """What a document asks for an object (ADR 0099 point 4).

    Attributes:
        UPSERT: Create the object or update it; the default.
        CREATE: Create it; refused when it exists.
        UPDATE: Update it; refused when it does not exist.
        DELETE: Delete it, with everything it holds.
    """

    UPSERT = "upsert"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class Entity:
    """One object of a document, or of the installation's current configuration.

    In a document ``fields`` holds only the fields the document names, so a field it leaves
    out keeps its value; in the current configuration it holds every field. The natural key
    is in ``key``, not in ``fields``.

    Attributes:
        kind: The name of the object's kind.
        key: The natural key within its parent; empty for a single object.
        action: What the document asks (always ``UPSERT`` in the current configuration).
        fields: The field values by name.
        children: The objects it holds, by collection name; a collection the document does
            not name is absent, one it names empty is an empty tuple.
        path: Where the object is in the document, for messages.
    """

    kind: str
    key: tuple[str, ...] = ()
    action: Action = Action.UPSERT
    fields: Mapping[str, Value] = field(default_factory=dict)
    children: Mapping[str, tuple["Entity", ...]] = field(default_factory=dict)
    path: str = ""


def path_of(parent: str, collection: str, key: tuple[str, ...]) -> str:
    """Return the path of an object: the parent's, then the collection and the key parts."""
    own = collection + "".join(f"[{part}]" for part in key)
    return f"{parent}.{own}" if parent else own


def walk(root: Entity, kind: str | None = None) -> Iterator[tuple[Address, Entity]]:
    """Yield every object below ``root`` with its address, parents before their children.

    Args:
        root: The tree to walk (the root itself is not yielded).
        kind: Only the objects of this kind, when given.
    """
    pending: deque[tuple[Address, Entity]] = deque([((), root)])
    while pending:
        address, entity = pending.popleft()
        for children in entity.children.values():
            for child in children:
                here = (*address, *child.key)
                if kind is None or child.kind == kind:
                    yield here, child
                pending.append((here, child))


type FieldChange = Callable[[str, Address, str, Value], Value]
"""Turns one field value into another, given the object's kind, its address and the field."""


def map_fields(entity: Entity, change: FieldChange, address: Address = ()) -> Entity:
    """Return ``entity`` with every field value, below it too, passed through ``change``."""
    fields = {
        name: change(entity.kind, address, name, value) for name, value in entity.fields.items()
    }
    children = {
        group: tuple(map_fields(child, change, (*address, *child.key)) for child in members)
        for group, members in entity.children.items()
    }
    return replace(entity, fields=fields, children=children)


def any_field(entity: Entity, test: Callable[[Value], bool]) -> bool:
    """Whether any field value of ``entity``, or of an object below it, passes ``test``."""
    return any(test(value) for value in entity.fields.values()) or any(
        any_field(child, test) for members in entity.children.values() for child in members
    )


def drop_fields(entity: Entity, test: Callable[[Value], bool]) -> Entity:
    """Return ``entity`` without the fields whose value passes ``test``, below it too."""
    fields = {name: value for name, value in entity.fields.items() if not test(value)}
    children = {
        group: tuple(drop_fields(child, test) for child in members)
        for group, members in entity.children.items()
    }
    return replace(entity, fields=fields, children=children)
