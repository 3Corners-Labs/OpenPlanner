"""What an actor may see of the configuration in an export (ADR 0099 point 11).

An export leaves out the objects the actor may not manage and the fields that need a
permission the actor lacks (a channel secret needs ``integrations.manage``, not only the
organisation settings); an organisation or a group stays as the frame of what it holds. This is
stricter than the portal, which shows some objects read-only
to roles that cannot change them: a document is a way to *change* the configuration elsewhere,
so it only carries what its holder could change here. The planner checks the same permissions
per change when the document is applied.
"""

from dataclasses import replace

from app.domain.authorization import Permission
from app.domain.configuration_changes import ChangeAction, MayChange
from app.domain.configuration_plan import permissions_of, target_for
from app.domain.configuration_schema import Anchor, Format, Kind
from app.domain.configuration_tree import Entity


def visible_to(root: Entity, fmt: Format, may: MayChange) -> Entity:
    """Return ``root`` without the objects and fields ``may`` does not allow.

    Args:
        root: The configuration tree to export.
        fmt: The format whose kinds say which permission each object and field needs.
        may: Whether the actor holds a permission on a target (the matrix).
    """
    return _filter(root, fmt.root, None, None, None, may)


def _allowed(
    permissions: frozenset[Permission], organisation: str | None, group: str | None, may: MayChange
) -> bool:
    """Whether every permission is held on the target given by where the object sits."""
    return all(may(p, target_for(p, organisation, group)) for p in permissions)


def _filter(
    entity: Entity,
    kind: Kind,
    organisation: str | None,
    group: str | None,
    base: Permission | None,
    may: MayChange,
) -> Entity:
    """Return ``entity`` with its fields and children filtered; its own right is checked above."""
    fields = dict(entity.fields)
    for spec in kind.fields:
        needed = permissions_of(kind, base, ChangeAction.UPDATE, (spec.name,))
        if spec.name in fields and not _allowed(needed, organisation, group, may):
            del fields[spec.name]
    children: dict[str, tuple[Entity, ...]] = {}
    for collection in kind.collections:
        members: list[Entity] = []
        for child in entity.children.get(collection.name, ()):
            place = _place(collection.kind, child, organisation, group, base)
            container = collection.kind.anchor is not None
            own = frozenset() if place[2] is None or container else frozenset({place[2]})
            if _allowed(own, place[0], place[1], may):
                members.append(_filter(child, collection.kind, *place, may))
        if members:
            children[collection.name] = tuple(members)
    return replace(entity, fields=fields, children=children)


def _place(
    kind: Kind, entity: Entity, organisation: str | None, group: str | None, base: Permission | None
) -> tuple[str | None, str | None, Permission | None]:
    """Return the organisation, group and permission that apply to an object and below it."""
    if kind.anchor is Anchor.ORGANISATION:
        organisation = entity.key[0]
    elif kind.anchor is Anchor.GROUP:
        group = entity.key[0]
    if kind.organisation_field is not None:
        owner = entity.fields.get(kind.organisation_field)
        organisation = owner if isinstance(owner, str) else organisation
    return organisation, group, kind.permission or base
