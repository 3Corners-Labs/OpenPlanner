"""Planning a configuration document (ADR 0099 points 4 to 7 and 11).

``plan_document`` compares a document with the installation's current configuration and
returns, without writing anything, what applying it would change and what stops it. Both are
trees of ``Entity`` objects, so this module knows no kind of object: the registry of the
document's format (``configuration_schema``) says what each kind's fields are, which rights
change it and which rules check it. The plan is the one code path that apply uses too: it
carries the values the adapter writes, lists a change by the *names* of the fields it
touches, and never shows a value.

The steps, in order: every object is resolved against its action (create, update, upsert,
delete) and the mode (merge, replace); the fields it names are normalised and checked; the
rules across objects run on what would result; every change is checked against the permission
of the screen that edits it; and the document's digest and the settings version make the plan
hash.
"""

import copy
import hashlib
from dataclasses import dataclass

from app.domain.authorization import LEVELS, Level, Permission, Target
from app.domain.configuration_changes import (
    Change,
    ChangeAction,
    MayChange,
    Refusal,
    Resolution,
    RuleContext,
    Usage,
)
from app.domain.configuration_document import Document, Mode
from app.domain.configuration_schema import (
    Anchor,
    Collection,
    FieldSpec,
    FieldType,
    Kind,
    Shape,
)
from app.domain.configuration_tree import Action, Address, Entity, path_of
from app.domain.configuration_values import Value, canonical_json
from app.domain.errors import DomainError, ErrorCode

_MISSING = object()
"""Stands for a field the current object does not have."""


@dataclass(frozen=True)
class Plan:
    """What applying a document would do, and what stops it.

    Attributes:
        changes: Every object written, parents first, then every object deleted.
        refusals: Everything that stops the plan; it can only be applied when there is none.
        digest: The plan hash: SHA-256 over the document's digest and the settings version it
            was computed against, so an apply is refused when the configuration moved on.
    """

    changes: tuple[Change, ...]
    refusals: tuple[Refusal, ...]
    digest: str

    @property
    def ok(self) -> bool:
        """Whether the plan can be applied: nothing refuses it."""
        return not self.refusals

    def lines(self) -> list[str]:
        """Describe each change by its action, kind, address and field names, never values."""
        lines: list[str] = []
        for change in self.changes:
            line = " ".join(
                filter(None, (change.action.value, change.kind, "/".join(change.address)))
            )
            if change.fields and change.action is not ChangeAction.DELETE:
                line += f": {', '.join(change.fields)}"
            lines.append(line)
        return lines


@dataclass(frozen=True)
class _Place:
    """Where an object sits: its path and address, and what is above it."""

    path: str = ""
    address: Address = ()
    organisation: str | None = None
    group: str | None = None
    permission: Permission | None = None
    root: bool = False


def plan_document(
    document: Document,
    current: Entity,
    *,
    usage: Usage,
    may_change: MayChange,
    context: RuleContext,
    settings_version: int,
) -> Plan:
    """Plan ``document`` against ``current`` without writing anything.

    Args:
        document: The document, read and checked for its structure.
        current: The installation's configuration as it is now, in the document's shape.
        usage: What the installation knows that its configuration does not.
        may_change: Whether the actor holds a permission on a target (the matrix).
        context: What the rules need from outside the document.
        settings_version: The configuration's version the plan is computed against.

    Returns:
        The plan: changes, refusals and the plan hash.
    """
    return _Planner(document, current, usage, may_change, context).run(settings_version)


class _Planner:
    """One run of the planner: collects writes, deletions and refusals while it walks."""

    def __init__(
        self,
        document: Document,
        current: Entity,
        usage: Usage,
        may_change: MayChange,
        context: RuleContext,
    ) -> None:
        """Keep the inputs of the run."""
        self._document = document
        self._current = current
        self._usage = usage
        self._may_change = may_change
        self._context = context
        self._writes: list[Change] = []
        self._deletes: list[Change] = []
        self._refusals: list[Refusal] = []

    def run(self, settings_version: int) -> Plan:
        """Walk the document, run the rules across objects and return the plan."""
        root = self._document.schema.root
        top = _Place(root=True)
        children: dict[str, tuple[Entity, ...]] = {}
        for coll in root.collections:
            existing = self._current.children.get(coll.name, ())
            desired = self._document.root.children.get(coll.name)
            children[coll.name] = (
                existing if desired is None else self._collection(coll, desired, existing, top)
            )
        resolution = Resolution(
            self._current,
            Entity(root.name, children=children),
            tuple(self._writes),
            tuple(self._deletes),
            self._usage,
            self._context,
        )
        for rule in self._document.schema.integrity:
            self._refusals.extend(rule(resolution))
        changes = tuple(self._writes) + tuple(self._deletes)
        effect = "\n".join(
            f"{c.action.value} {c.kind} {'/'.join(c.address)} {','.join(c.fields)}" for c in changes
        )
        digest = hashlib.sha256(
            f"{self._document.digest}:{settings_version}:{effect}".encode()
        ).hexdigest()
        return Plan(changes, tuple(self._refusals), digest)

    def _refuse(self, path: str, code: ErrorCode, field: str | None = None) -> None:
        """Record one refusal."""
        self._refusals.append(Refusal(path, code, field))

    def _collection(
        self,
        coll: Collection,
        desired: tuple[Entity, ...],
        current: tuple[Entity, ...],
        place: _Place,
    ) -> tuple[Entity, ...]:
        """Resolve the objects of a collection the document names; return the result."""
        existing = {entity.key: entity for entity in current}
        result = dict(existing)
        for wanted in desired:
            outcome = self._entity(coll, wanted, existing.get(wanted.key), place)
            if outcome is None:
                result.pop(wanted.key, None)
            else:
                result[wanted.key] = outcome
        if self._document.mode is Mode.REPLACE and coll.shape is not Shape.SINGLE:
            listed = {wanted.key for wanted in desired}
            for key, entity in existing.items():
                if key in listed or (
                    place.root and not self._in_scope(coll.kind, key, None, entity)
                ):
                    continue
                if self._delete(coll, entity, path_of(place.path, coll.name, key), place):
                    result.pop(key, None)
        return tuple(result.values())

    def _in_scope(
        self, kind: Kind, key: tuple[str, ...], named: Entity | None, current: Entity | None
    ) -> bool:
        """Whether an object at the top of the tree is inside the document's scope."""
        organisation = self._document.scope.organisation
        if organisation is None:
            return True
        if kind.anchor is Anchor.ORGANISATION:
            return key == (organisation,)
        if kind.organisation_field is None:
            return False
        owners = [
            entity.fields.get(kind.organisation_field)
            for entity in (named, current)
            if entity is not None
        ]
        known = [owner for owner in owners if owner is not None]
        return bool(known) and all(owner == organisation for owner in known)

    def _entity(
        self, coll: Collection, wanted: Entity, current: Entity | None, place: _Place
    ) -> Entity | None:
        """Resolve one object against its action; return what it is afterwards, or ``None``."""
        kind = coll.kind
        if place.root and not self._in_scope(kind, wanted.key, wanted, current):
            self._refuse(wanted.path, ErrorCode.CONFIGURATION_OUT_OF_SCOPE)
            return current
        if wanted.action is Action.DELETE:
            if current is None:
                return None
            for name in wanted.children:
                self._refuse(path_of(wanted.path, name, ()), ErrorCode.INVALID_CONFIGURATION)
            return current if not self._delete(coll, current, wanted.path, place) else None
        if wanted.action is Action.CREATE and current is not None:
            self._refuse(wanted.path, ErrorCode.CONFIGURATION_OBJECT_EXISTS)
            return current
        if wanted.action is Action.UPDATE and current is None:
            self._refuse(wanted.path, ErrorCode.CONFIGURATION_OBJECT_MISSING)
            return None
        return self._write(kind, wanted, current, place)

    def _write(self, kind: Kind, wanted: Entity, current: Entity | None, place: _Place) -> Entity:
        """Create or update one object: check its fields, emit the change, resolve its children."""
        create = current is None
        path = wanted.path
        given: dict[str, Value] = {}
        refused: set[str] = set()
        valid = True
        for name, value in wanted.fields.items():
            spec = kind.field(name)
            if spec.check is not None and value is not None:
                try:
                    value = spec.check(value, self._context)  # noqa: PLW2901 - normalised value
                except DomainError as error:
                    self._refuse(path, error.code, error.field or name)
                    refused.add(name)
                    valid = False
                    continue
            given[name] = value
        base: dict[str, Value] = dict(current.fields) if current is not None else {}
        if create:
            valid = self._defaults(kind, given | dict.fromkeys(refused), base, path) and valid
        merged = {**base, **given}
        if valid and kind.check is not None and (create or _differs(kind, current, given)):
            keys = dict(zip(kind.key, wanted.key, strict=True))
            before = None if current is None else {**keys, **current.fields}
            try:
                checked = kind.check({**keys, **merged}, before, self._context)
                merged = {name: value for name, value in checked.items() if name not in keys}
            except DomainError as error:
                self._refuse(path, error.code, error.field)
        names = [name for name in given if create or _changed(kind, current, name, merged)]
        here = self._place(kind, wanted, merged, place)
        unchanged = tuple(
            name
            for name in given
            if kind.field(name).type is FieldType.SECRET and name not in names
        )
        if unchanged and not create:
            # Naming a secret is a probe whether it is right: ask for its screen's permission
            # even when it is the same, so a refusal never tells who may not manage it.
            self._authorise_named(kind, wanted, here, unchanged, merged)
        if create or names:
            action = ChangeAction.CREATE if create else ChangeAction.UPDATE
            self._emit(kind, wanted, here, action, tuple(names), merged)
        return Entity(
            kind.name,
            wanted.key,
            Action.UPSERT,
            merged,
            self._children(kind, wanted, current, here),
            path,
        )

    def _defaults(
        self, kind: Kind, given: dict[str, Value], base: dict[str, Value], path: str
    ) -> bool:
        """Fill a new object's fields with their defaults; refuse a missing required one."""
        complete = True
        for spec in kind.value_fields:
            if spec.name in given:
                continue
            if spec.required:
                self._refuse(path, ErrorCode.INVALID_CONFIGURATION, spec.name)
                complete = False
            elif spec.type is not FieldType.SECRET:
                base[spec.name] = copy.deepcopy(spec.default)
        return complete

    def _children(
        self, kind: Kind, wanted: Entity, current: Entity | None, place: _Place
    ) -> dict[str, tuple[Entity, ...]]:
        """Resolve the collections an object holds; one the document leaves out is untouched."""
        children: dict[str, tuple[Entity, ...]] = {}
        for coll in kind.collections:
            existing = current.children.get(coll.name, ()) if current is not None else ()
            desired = wanted.children.get(coll.name)
            children[coll.name] = (
                existing if desired is None else self._collection(coll, desired, existing, place)
            )
        return children

    def _place(self, kind: Kind, wanted: Entity, fields: dict[str, Value], above: _Place) -> _Place:
        """Return where an object sits: its address and the organisation and group over it."""
        organisation, group = above.organisation, above.group
        if kind.anchor is Anchor.ORGANISATION:
            organisation = wanted.key[0]
        elif kind.anchor is Anchor.GROUP:
            group = wanted.key[0]
        if kind.organisation_field is not None:
            owner = fields.get(kind.organisation_field)
            organisation = owner if isinstance(owner, str) else organisation
        return _Place(
            wanted.path,
            above.address + wanted.key,
            organisation,
            group,
            kind.permission or above.permission,
        )

    def _emit(
        self,
        kind: Kind,
        wanted: Entity,
        here: _Place,
        action: ChangeAction,
        names: tuple[str, ...],
        values: dict[str, Value],
    ) -> None:
        """Record one change after checking the actor may make it."""
        change = Change(
            kind=kind.name,
            key=wanted.key,
            address=here.address,
            path=wanted.path,
            action=action,
            fields=names,
            values=dict(values),
            permissions=_permissions(kind, here.permission, action, names),
            organisation=here.organisation,
            group=here.group,
        )
        self._authorise(change)
        if action is ChangeAction.DELETE:
            self._deletes.append(change)
        else:
            self._writes.append(change)

    def _authorise_named(
        self,
        kind: Kind,
        wanted: Entity,
        here: _Place,
        names: tuple[str, ...],
        values: dict[str, Value],
    ) -> None:
        """Check the permission of fields a document names without changing them."""
        self._authorise(
            Change(
                kind=kind.name,
                key=wanted.key,
                address=here.address,
                path=wanted.path,
                action=ChangeAction.UPDATE,
                fields=names,
                permissions=_permissions(kind, here.permission, ChangeAction.UPDATE, names),
                organisation=here.organisation,
                group=here.group,
            )
        )

    def _authorise(self, change: Change) -> None:
        """Refuse the change when the actor lacks any permission its screens require."""
        for permission in sorted(change.permissions):
            target = _target(permission, change.organisation, change.group)
            if not self._may_change(permission, target):
                self._refuse(change.path, ErrorCode.NOT_PERMITTED)
                return

    def _delete(self, coll: Collection, entity: Entity, path: str, above: _Place) -> bool:
        """Delete an object with what it holds; refuse it while something refers to it."""
        kind = coll.kind
        if not kind.deletable:
            self._refuse(path, ErrorCode.INVALID_CONFIGURATION)
            return False
        if self._in_use(kind, entity, above.address):
            self._refuse(path, ErrorCode.CONFIGURATION_OBJECT_IN_USE)
            return False
        wanted = Entity(kind.name, entity.key, Action.DELETE, {}, {}, path)
        here = self._place(kind, wanted, dict(entity.fields), above)
        self._emit(kind, wanted, here, ChangeAction.DELETE, (), {})
        return True

    def _in_use(self, kind: Kind, entity: Entity, above: Address) -> bool:
        """Whether something outside the configuration refers to the object or what it holds."""
        address = above + entity.key
        if address in self._usage.in_use.get(kind.name, frozenset()):
            return True
        return any(
            self._in_use(coll.kind, child, address)
            for coll in kind.collections
            for child in entity.children.get(coll.name, ())
        )


def _differs(kind: Kind, current: Entity | None, given: dict[str, Value]) -> bool:
    """Whether any field the document gave differs from the current object's."""
    return any(_changed(kind, current, name, given) for name in given)


def _changed(kind: Kind, current: Entity | None, name: str, values: dict[str, Value]) -> bool:
    """Whether the field ``name`` as it will be differs from the current object's."""
    if current is None:
        return True
    before = current.fields.get(name, _MISSING)
    return before is _MISSING or not _same(kind.field(name), before, values[name])  # type: ignore[arg-type]


def _same(spec: FieldSpec, before: Value, after: Value) -> bool:
    """Whether two values of a field are the same; an unordered list is compared as a set."""
    if (
        spec.type in (FieldType.STRINGS, FieldType.OBJECTS)
        and not spec.ordered
        and isinstance(before, list)
        and isinstance(after, list)
    ):
        return sorted(canonical_json(item) for item in before) == sorted(
            canonical_json(item) for item in after
        )
    return bool(before == after)


def _permissions(
    kind: Kind, base: Permission | None, action: ChangeAction, names: tuple[str, ...]
) -> frozenset[Permission]:
    """Return what the admin screens of an object's fields require to make a change."""
    permissions: set[Permission] = set()
    if action is ChangeAction.CREATE:
        own = kind.create_permission or base
    elif action is ChangeAction.DELETE:
        own = kind.delete_permission or base
    else:
        own = None
    if own is not None:
        permissions.add(own)
    for name in names:
        override = kind.field(name).permission
        if override is not None:
            permissions.add(override)
        elif action is ChangeAction.UPDATE and base is not None:
            permissions.add(base)
    return frozenset(permissions)


def _target(permission: Permission, organisation: str | None, group: str | None) -> Target:
    """Return the target a permission is checked against, from where the object sits.

    Raises:
        ValueError: The object does not sit where the permission's level needs (a registry
            mistake, which the tests of the format catch).
    """
    level = LEVELS[permission]
    if level is Level.GLOBAL:
        return Target.everything()
    if level is Level.ORGANISATION:
        if organisation is None:
            raise ValueError(f"{permission} needs an organisation")
        return Target.for_organisation(organisation)
    if organisation is None or group is None:
        raise ValueError(f"{permission} needs a group")
    return Target.for_group(group, organisation)
