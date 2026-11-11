"""The shape of a configuration document (ADR 0099): kinds of objects, fields and children.

A *format* is a registry of kinds. A kind says what an object is called, which fields address
it (its natural key), which fields it has, which kinds of object it holds and which
configuration tables it covers; a field says its type, bounds and default. Parsing, planning,
the JSON Schema and the test that every configuration table is reached all read this one
registry, so a format is declared once and cannot drift (ADR 0005). The version-1 registry is
``app.domain.configuration_v1``; the tests use small registries of their own.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.domain.authorization import Permission
from app.domain.configuration_changes import IntegrityRule, RuleContext
from app.domain.configuration_values import Json, Value

_UNSET: Final = object()
"""Tells a helper that no default was given, which a ``None`` default cannot."""


class FieldType(StrEnum):
    """What a field holds, which decides how a document's value is read and checked.

    Attributes:
        STRING: A text.
        INTEGER: A whole number (never a boolean).
        BOOLEAN: ``true`` or ``false``.
        JSON: A JSON object whose inside a rule of the kind checks (contacts, theme tokens).
        SETTING: A JSON scalar whose type the settings registry gives for its key.
        SECRET: A secret, sealed in the document.
        BINARY: A file: media type, SHA-256 and base64 bytes.
        STRINGS: A list of texts.
        OBJECTS: A list of objects, each described by the field's ``items``.
    """

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    JSON = "json"
    SETTING = "setting"
    SECRET = "secret"  # noqa: S105  # nosec B105 - a type name, not a password
    BINARY = "binary"
    STRINGS = "strings"
    OBJECTS = "objects"


class Shape(StrEnum):
    """How a kind's objects appear under their parent in a document.

    Attributes:
        ENTITIES: A JSON array of objects, each addressed by its natural key.
        MAP: A JSON object from key to value, nested once per key part (settings, texts).
        SINGLE: One JSON object: the only object of its kind in its parent.
        KEYS: A JSON array of texts, each the key of an object that has no other fields.
    """

    ENTITIES = "entities"
    MAP = "map"
    SINGLE = "single"
    KEYS = "keys"


class Anchor(StrEnum):
    """What an object's natural key names, for checking rights against the right target.

    Attributes:
        ORGANISATION: The first key part is an organisation's slug.
        GROUP: The first key part is a group's slug, inside the organisation above it.
    """

    ORGANISATION = "organisation"
    GROUP = "group"


type FieldCheck = Callable[[Value, RuleContext], Value]
"""A rule for one field's value: returns it normalised (as the portal stores it) or raises
``DomainError``; the error carries a field name, never a value."""

type KindCheck = Callable[
    [Mapping[str, Value], Mapping[str, Value] | None, RuleContext], Mapping[str, Value]
]
"""A rule for the whole object: gets every field as it will be, the key fields among them, and
the current fields in the same form (``None`` on create); returns the fields normalised or
raises ``DomainError``."""


@dataclass(frozen=True)
class FieldSpec:
    """One field of a kind.

    Use the helpers (``text``, ``integer``, …) rather than the constructor.

    Attributes:
        name: The JSON property.
        type: What the field holds.
        required: Whether a created object must state it (no default, never null).
        nullable: Whether ``null`` is a value of the field.
        default: What a created object gets when the document leaves the field out.
        choices: The allowed texts, for a text with a closed set.
        minimum: The smallest allowed number.
        maximum: The largest allowed number.
        max_length: The longest allowed text.
        items: For a list of objects, the fields of each item.
        ordered: Whether the order of a list matters (otherwise it is compared as a set).
        permission: The permission this field needs when it differs from the kind's.
        check: A rule for the field's value.
        description: One sentence for the generated JSON Schema.
        columns: The columns of the kind's tables this field covers, when they are not simply
            the field's own name (a field that spans several columns, or has another name).
    """

    name: str
    type: FieldType
    required: bool = False
    nullable: bool = False
    default: Json = None
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    items: tuple["FieldSpec", ...] = ()
    ordered: bool = False
    permission: Permission | None = None
    check: FieldCheck | None = None
    description: str = ""
    columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a field that contradicts itself."""
        if self.required and (self.nullable or self.default is not None):
            raise ValueError(f"{self.name}: a required field has no default and is never null")
        if self.type is FieldType.OBJECTS and not self.items:
            raise ValueError(f"{self.name}: a list of objects names its items")
        plain = {
            FieldType.STRING,
            FieldType.INTEGER,
            FieldType.BOOLEAN,
            FieldType.STRINGS,
            FieldType.JSON,
        }
        if any(item.type not in plain for item in self.items):
            raise ValueError(f"{self.name}: the items of a list hold plain values only")


def _field(
    name: str,
    type_: FieldType,
    *,
    required: bool,
    nullable: bool,
    default: object,
    **rest: object,
) -> FieldSpec:
    """Build a field: no default and not nullable means it must be given on create."""
    given = default is not _UNSET
    if required and (nullable or given):
        raise ValueError(f"{name}: a required field has no default and is never null")
    return FieldSpec(
        name,
        type_,
        required=required or (not given and not nullable),
        nullable=nullable,
        default=default if given else None,  # type: ignore[arg-type]
        **rest,  # type: ignore[arg-type]
    )


def text(
    name: str,
    *,
    required: bool = False,
    nullable: bool = False,
    default: object = _UNSET,
    max_length: int | None = None,
    choices: tuple[str, ...] = (),
    permission: Permission | None = None,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a text field."""
    return _field(
        name,
        FieldType.STRING,
        required=required,
        nullable=nullable,
        default=default,
        max_length=max_length,
        choices=choices,
        permission=permission,
        check=check,
        description=description,
        columns=columns,
    )


def integer(
    name: str,
    *,
    required: bool = False,
    nullable: bool = False,
    default: object = _UNSET,
    minimum: int | None = None,
    maximum: int | None = None,
    permission: Permission | None = None,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a whole-number field."""
    return _field(
        name,
        FieldType.INTEGER,
        required=required,
        nullable=nullable,
        default=default,
        minimum=minimum,
        maximum=maximum,
        permission=permission,
        check=check,
        description=description,
        columns=columns,
    )


def boolean(
    name: str,
    *,
    required: bool = False,
    nullable: bool = False,
    default: object = _UNSET,
    permission: Permission | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a true-or-false field."""
    return _field(
        name,
        FieldType.BOOLEAN,
        required=required,
        nullable=nullable,
        default=default,
        permission=permission,
        description=description,
        columns=columns,
    )


def json_object(
    name: str,
    *,
    nullable: bool = False,
    default: object = _UNSET,
    permission: Permission | None = None,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a field that holds a JSON object, whose inside a rule checks."""
    return _field(
        name,
        FieldType.JSON,
        required=False,
        nullable=nullable,
        default=default,
        permission=permission,
        check=check,
        description=description,
        columns=columns,
    )


def setting(
    name: str,
    *,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe the value of a setting: a JSON scalar of the type its registry entry gives."""
    return _field(
        name,
        FieldType.SETTING,
        required=True,
        nullable=False,
        default=_UNSET,
        check=check,
        description=description,
        columns=columns,
    )


def secret(
    name: str,
    *,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a secret: optional, sealed in a document, never cleared by leaving it out."""
    return _field(
        name,
        FieldType.SECRET,
        required=False,
        nullable=True,
        default=_UNSET,
        check=check,
        description=description,
        columns=columns,
    )


def binary(
    name: str,
    *,
    nullable: bool = False,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a file: media type, SHA-256 and bytes."""
    return _field(
        name,
        FieldType.BINARY,
        required=not nullable,
        nullable=nullable,
        default=_UNSET,
        check=check,
        description=description,
        columns=columns,
    )


def strings(
    name: str,
    *,
    ordered: bool = False,
    max_length: int | None = None,
    permission: Permission | None = None,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a list of texts, empty unless the document says otherwise."""
    return _field(
        name,
        FieldType.STRINGS,
        required=False,
        nullable=False,
        default=[],
        ordered=ordered,
        max_length=max_length,
        permission=permission,
        check=check,
        description=description,
        columns=columns,
    )


def objects(
    name: str,
    *,
    items: tuple[FieldSpec, ...],
    ordered: bool = False,
    permission: Permission | None = None,
    check: FieldCheck | None = None,
    description: str = "",
    columns: tuple[str, ...] = (),
) -> FieldSpec:
    """Describe a list of objects, replaced as a whole when the document names it."""
    return _field(
        name,
        FieldType.OBJECTS,
        required=False,
        nullable=False,
        default=[],
        items=items,
        ordered=ordered,
        permission=permission,
        check=check,
        description=description,
        columns=columns,
    )


@dataclass(frozen=True)
class Collection:
    """The objects of one kind under a parent, and how a document writes them.

    Attributes:
        name: The JSON property of the parent that holds them.
        kind: What they are.
        shape: How they appear (an array, a map, or one object).
        description: One sentence for the generated JSON Schema.
    """

    name: str
    kind: "Kind"
    shape: Shape = Shape.ENTITIES
    description: str = ""


def _fits(spec: FieldSpec, value: Json) -> bool:
    """Whether ``value`` is a value the field ``spec`` can hold (the type, and the choices)."""
    if spec.type is FieldType.BOOLEAN:
        return isinstance(value, bool)
    if spec.type is FieldType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.type is FieldType.STRING:
        return isinstance(value, str) and (not spec.choices or value in spec.choices)
    return False


@dataclass(frozen=True)
class Kind:
    """A kind of object in a configuration document.

    Attributes:
        name: The kind's name, as a plan shows it.
        key: The fields that address an object within its parent (its natural key).
        permission: What the admin screen of this object requires to change it; ``None`` for
            the root, and for a kind that follows its parent.
        create_permission: What creating one requires, when it differs from ``permission``.
        delete_permission: What deleting one requires, when it differs from ``permission``.
        tables: The configuration tables this kind covers (ADR 0089 point 2).
        fields: Every field, the key fields first.
        collections: The kinds of object it holds.
        check: A rule for the whole object, run on its fields as they will be.
        anchor: What the key names, when it is an organisation or a group.
        organisation_field: The field that names the organisation of an object that sits at
            the top of the tree but belongs to one (an API client).
        deletable: Whether a document may delete one.
        archive: ``(field, value)`` when an object that something still refers to is archived
            instead of refused: the field that marks it and the value that means archived. An
            object that nothing refers to is deleted as before.
        retain_omitted: Whether a ``replace`` document keeps the objects of this kind that it
            does not list (history that is never trimmed by leaving it out).
        description: One sentence for the generated JSON Schema.
        derived: The columns of its tables that are deliberately not in a document because
            the adapter sets them (``table.column``: timestamps, digests, version counters).
    """

    name: str
    key: tuple[str, ...] = ()
    permission: Permission | None = None
    create_permission: Permission | None = None
    delete_permission: Permission | None = None
    tables: frozenset[str] = frozenset()
    fields: tuple[FieldSpec, ...] = ()
    collections: tuple[Collection, ...] = ()
    check: KindCheck | None = None
    anchor: Anchor | None = None
    organisation_field: str | None = None
    deletable: bool = True
    archive: tuple[str, Json] | None = None
    retain_omitted: bool = False
    description: str = ""
    derived: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Refuse a key that names a field the kind does not have."""
        names = {spec.name for spec in self.fields}
        missing = [name for name in self.key if name not in names]
        if missing:
            raise ValueError(f"{self.name}: key fields {missing} are not fields of the kind")
        if self.archive is not None:
            if self.archive[0] not in names:
                raise ValueError(f"{self.name}: the archive field {self.archive[0]} is not a field")
            spec = next(field for field in self.fields if field.name == self.archive[0])
            if not _fits(spec, self.archive[1]):
                raise ValueError(f"{self.name}: the archive value does not fit {spec.name}")

    def field(self, name: str) -> FieldSpec:
        """Return the field called ``name``.

        Raises:
            KeyError: The kind has no such field.
        """
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def collection(self, name: str) -> Collection:
        """Return the collection called ``name``.

        Raises:
            KeyError: The kind holds no such collection.
        """
        for found in self.collections:
            if found.name == name:
                return found
        raise KeyError(name)

    @property
    def value_fields(self) -> tuple[FieldSpec, ...]:
        """The fields that are not part of the key."""
        return tuple(spec for spec in self.fields if spec.name not in self.key)

    @property
    def value_field(self) -> FieldSpec:
        """The one field that is not part of the key: the value of a map entry.

        Raises:
            ValueError: The kind does not have exactly one such field.
        """
        found = self.value_fields
        if len(found) != 1:
            raise ValueError(f"{self.name}: a map entry has exactly one value field")
        return found[0]


@dataclass(frozen=True)
class Format:
    """A version of the document format: the registry of its kinds.

    Attributes:
        name: The value of a document's ``format`` property, for example
            ``openplanner.config/v1``.
        root: The kind of the installation itself; its collections are the document's
            top-level sections.
        integrity: The rules across objects, run on what a document would do.
    """

    name: str
    root: Kind
    integrity: tuple[IntegrityRule, ...] = ()

    def kinds(self) -> Iterator[Kind]:
        """Yield every kind once, the root first, parents before their children."""
        seen: set[str] = set()
        pending = [self.root]
        while pending:
            kind = pending.pop(0)
            if kind.name in seen:
                continue
            seen.add(kind.name)
            yield kind
            pending.extend(found.kind for found in kind.collections)

    def kind(self, name: str) -> Kind:
        """Return the kind called ``name``.

        Raises:
            KeyError: The format has no such kind.
        """
        for kind in self.kinds():
            if kind.name == name:
                return kind
        raise KeyError(name)

    def tables(self) -> frozenset[str]:
        """Return every configuration table some kind of this format covers."""
        return frozenset(table for kind in self.kinds() for table in kind.tables)
