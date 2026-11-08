"""A configuration document (ADR 0099): strict reading, natural keys, and writing back.

A document is JSON that states the desired configuration of an installation (or of one
organisation) as a tree of objects addressed by natural keys, never by database ids. This
module reads a decoded JSON object into that tree against a format registry
(``app.domain.configuration_schema``) and writes a tree back as JSON. Reading is strict: an
unknown field, a value of the wrong type or outside its bounds, a secret that is not sealed
or a file whose hash does not match is refused with the JSON path of the offence, never its
value. What the document would change is the planner's work (``configuration_plan``).
"""

import base64
import binascii
import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import cast

from app.domain.configuration_schema import Collection, FieldSpec, FieldType, Format, Kind, Shape
from app.domain.configuration_tree import Action, Entity, path_of
from app.domain.configuration_values import (
    Binary,
    Json,
    Sealed,
    Secret,
    Value,
    document_digest,
)
from app.domain.errors import DomainError, ErrorCode

HEADER = ("format", "mode", "scope", "meta", "seal")
"""The top-level properties that are not sections of the installation."""

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SEALED_KEYS = frozenset({"sealed"})
_BINARY_KEYS = frozenset({"media_type", "sha256", "data"})
_SCALARS = (str, int, float, bool)


DOCUMENT_FILENAME = "openplanner-configuration-{installation}-{date}.json"
"""The download name of an exported document."""


def document_filename(installation: str, exported_at: datetime) -> str:
    """Return the download name of a document exported on ``exported_at``.

    The installation name is turned into a slug (lower case, every character outside
    ``[a-z0-9.-]`` becomes ``-``) so the name always passes the filename rule.
    """
    slug = re.sub(r"[^a-z0-9.-]", "-", (installation or "openplanner").lower())
    return DOCUMENT_FILENAME.format(installation=slug, date=exported_at.strftime("%Y%m%d"))


class Mode(StrEnum):
    """What a document does with objects it does not name (ADR 0099 point 5).

    Attributes:
        MERGE: Leave them as they are; the default.
        REPLACE: Delete, in every collection the document names, what it does not list.
    """

    MERGE = "merge"
    REPLACE = "replace"


@dataclass(frozen=True)
class Scope:
    """Where a document may reach: the whole platform, or one organisation.

    Attributes:
        organisation: The organisation's slug; ``None`` for the platform.
    """

    organisation: str | None = None

    @property
    def is_platform(self) -> bool:
        """Whether the scope is the whole platform."""
        return self.organisation is None


@dataclass(frozen=True)
class Document:
    """A configuration document, read.

    Attributes:
        format: The format version the document states.
        mode: What happens to objects the document does not name.
        scope: The part of the installation it may change.
        root: The installation as the document states it.
        digest: SHA-256 of the canonical form of what was read, for the plan hash.
        schema: The format registry the document was read with, which writes it back.
        meta: Informational header (installation, export time, release); never applied.
        seal: The header of sealed values (key derivation, verifier); the adapter's.
    """

    format: str
    mode: Mode
    scope: Scope
    root: Entity
    digest: str
    schema: Format = field(repr=False, compare=False)
    meta: Mapping[str, Json] = field(default_factory=dict)
    seal: Mapping[str, Json] | None = None


def _invalid(path: str, code: ErrorCode = ErrorCode.INVALID_CONFIGURATION) -> DomainError:
    """Return the refusal that names ``path``."""
    return DomainError(code, path or None)


def parse_document(data: object, formats: Mapping[str, Format]) -> Document:
    """Read a decoded JSON document against the format it states.

    Args:
        data: The decoded JSON (the adapter refuses duplicate keys when it decodes).
        formats: Every format version the application reads, by name.

    Raises:
        DomainError: ``configuration_format_unsupported`` for an unknown ``format``,
            ``configuration_unknown_field`` for a property the format does not have, and
            ``invalid_configuration`` for anything else wrong, with the JSON path as field.
    """
    if not isinstance(data, Mapping):
        raise _invalid("")
    name = data.get("format")
    if not isinstance(name, str) or name not in formats:
        raise _invalid("format", ErrorCode.CONFIGURATION_FORMAT_UNSUPPORTED)
    fmt = formats[name]
    sections = {section.name: section for section in fmt.root.collections}
    for prop in data:
        if prop not in HEADER and prop not in sections:
            raise _invalid(str(prop), ErrorCode.CONFIGURATION_UNKNOWN_FIELD)
    children = {
        section.name: _read_collection(section, data[section.name], "")
        for section in fmt.root.collections
        if section.name in data
    }
    return Document(
        format=name,
        mode=_read_mode(data.get("mode", Mode.MERGE.value)),
        scope=_read_scope(data.get("scope", "platform")),
        root=Entity(kind=fmt.root.name, children=children),
        digest=document_digest(cast("Json", data)),
        schema=fmt,
        meta=_read_object(data.get("meta", {}), "meta"),
        seal=_read_object(data["seal"], "seal") if "seal" in data else None,
    )


def _read_object(raw: object, path: str) -> dict[str, Json]:
    """Return a header object: any JSON object, copied."""
    if not isinstance(raw, dict):
        raise _invalid(path)
    return copy.deepcopy(raw)


def _read_mode(raw: object) -> Mode:
    """Read ``mode``."""
    if isinstance(raw, str) and raw in Mode:
        return Mode(raw)
    raise _invalid("mode")


def _read_scope(raw: object) -> Scope:
    """Read ``scope``: ``"platform"`` or ``{"organisation": "<slug>"}``."""
    if raw == "platform":
        return Scope()
    if not isinstance(raw, Mapping):
        raise _invalid("scope")
    for prop in raw:
        if prop != "organisation":
            raise _invalid(f"scope.{prop}", ErrorCode.CONFIGURATION_UNKNOWN_FIELD)
    slug = raw.get("organisation")
    if not isinstance(slug, str) or not slug:
        raise _invalid("scope")
    return Scope(slug)


def _read_collection(coll: Collection, raw: object, parent: str) -> tuple[Entity, ...]:
    """Read the objects of one collection, as its shape says they are written."""
    where = path_of(parent, coll.name, ())
    if coll.shape is Shape.SINGLE:
        if not isinstance(raw, Mapping):
            raise _invalid(where)
        return (_read_entity(coll.kind, raw, where, ()),)
    if coll.shape is Shape.MAP:
        if not isinstance(raw, Mapping):
            raise _invalid(where)
        return _read_map(coll, raw, parent)
    if coll.shape is Shape.KEYS:
        return _read_keys(coll, raw, parent)
    if not isinstance(raw, list):
        raise _invalid(where)
    entities: list[Entity] = []
    seen: set[tuple[str, ...]] = set()
    for index, item in enumerate(raw):
        position = f"{where}[{index}]"
        if not isinstance(item, Mapping):
            raise _invalid(position)
        key = _read_key(coll.kind, item, position)
        entity = _read_entity(coll.kind, item, path_of(parent, coll.name, key), key)
        if key in seen:
            raise _invalid(entity.path)
        seen.add(key)
        entities.append(entity)
    return tuple(entities)


def _read_keys(coll: Collection, raw: object, parent: str) -> tuple[Entity, ...]:
    """Read a list of texts into one field-less object per text; a text may not repeat."""
    where = path_of(parent, coll.name, ())
    if not isinstance(raw, list):
        raise _invalid(where)
    spec = coll.kind.field(coll.kind.key[0])
    entities: dict[str, Entity] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            raise _invalid(f"{where}[{index}]")
        text = _key_text(spec, item, f"{where}[{index}]")
        path = path_of(parent, coll.name, (text,))
        if text in entities:
            raise _invalid(path)
        entities[text] = Entity(coll.kind.name, (text,), Action.UPSERT, {}, {}, path)
    return tuple(entities.values())


def _read_key(kind: Kind, raw: Mapping[object, object], position: str) -> tuple[str, ...]:
    """Read the natural key of an object; its path is its position until it is known."""
    parts: list[str] = []
    for name in kind.key:
        value = raw.get(name)
        spec = kind.field(name)
        if not isinstance(value, str) or not value:
            raise _invalid(f"{position}.{name}")
        parts.append(_key_text(spec, value, f"{position}.{name}"))
    return tuple(parts)


def _key_text(spec: FieldSpec, raw: str, path: str) -> str:
    """Read one part of a natural key trimmed, as every text is, so " acme" is the key "acme"."""
    key = _read_text(spec, raw, path)
    if not key:
        raise _invalid(path)
    return key


def _read_entity(
    kind: Kind, raw: Mapping[object, object], path: str, key: tuple[str, ...]
) -> Entity:
    """Read one object: its action, the fields it names, and the collections it holds."""
    names = {spec.name for spec in kind.fields}
    collections = {coll.name for coll in kind.collections}
    for prop in raw:
        if prop != "action" and prop not in names and prop not in collections:
            raise _invalid(f"{path}.{prop}", ErrorCode.CONFIGURATION_UNKNOWN_FIELD)
    fields = {
        spec.name: _read_value(spec, raw[spec.name], f"{path}.{spec.name}")
        for spec in kind.value_fields
        if spec.name in raw
    }
    children = {
        coll.name: _read_collection(coll, raw[coll.name], path)
        for coll in kind.collections
        if coll.name in raw
    }
    return Entity(kind.name, key, _read_action(raw.get("action"), path), fields, children, path)


def _read_action(raw: object, path: str) -> Action:
    """Read an object's ``action``; an object without one is an upsert."""
    if raw is None:
        return Action.UPSERT
    if isinstance(raw, str) and raw in Action:
        return Action(raw)
    raise _invalid(f"{path}.action")


def _read_map(coll: Collection, raw: Mapping[object, object], parent: str) -> tuple[Entity, ...]:
    """Read a map nested once per key part into one object per entry; ``null`` removes one."""
    kind = coll.kind
    leaf = kind.value_field
    entities: list[Entity] = []

    def walk(level: Mapping[object, object], key: tuple[str, ...]) -> None:
        for part, value in level.items():
            if not isinstance(part, str) or not part:
                raise _invalid(path_of(parent, coll.name, key))
            text = _key_text(kind.field(kind.key[len(key)]), part, path_of(parent, coll.name, key))
            here = (*key, text)
            where = path_of(parent, coll.name, here)
            if len(here) < len(kind.key):
                if not isinstance(value, Mapping):
                    raise _invalid(where)
                walk(value, here)
                continue
            if value is None:
                entities.append(Entity(kind.name, here, Action.DELETE, {}, {}, where))
                continue
            fields = {leaf.name: _read_value(leaf, value, where)}
            entities.append(Entity(kind.name, here, Action.UPSERT, fields, {}, where))

    walk(raw, ())
    return tuple(entities)


def _read_value(spec: FieldSpec, raw: object, path: str) -> Value:
    """Read one field value by the field's type; refuse what does not fit it."""
    if raw is None:
        if spec.nullable:
            return None
        raise _invalid(path)
    match spec.type:
        case FieldType.STRING:
            return _read_text(spec, raw, path)
        case FieldType.INTEGER:
            return _read_integer(spec, raw, path)
        case FieldType.BOOLEAN:
            if not isinstance(raw, bool):
                raise _invalid(path)
            return raw
        case FieldType.JSON:
            if not isinstance(raw, dict):
                raise _invalid(path)
            return copy.deepcopy(raw)
        case FieldType.SETTING:
            if not isinstance(raw, _SCALARS):
                raise _invalid(path)
            return raw
        case FieldType.SECRET:
            return _read_sealed(raw, path)
        case FieldType.BINARY:
            return _read_binary(raw, path)
        case FieldType.STRINGS:
            return _read_strings(spec, raw, path)
        case FieldType.OBJECTS:
            return _read_objects(spec, raw, path)
    raise AssertionError(spec.type)  # pragma: no cover - every FieldType is handled above


def _read_text(spec: FieldSpec, raw: object, path: str) -> str:
    """Read a text, trimmed as the portal trims what an admin types.

    It must be within the field's length and, if the field has a closed set, among its choices.
    """
    if not isinstance(raw, str):
        raise _invalid(path)
    raw = raw.strip()
    if spec.max_length is not None and len(raw) > spec.max_length:
        raise _invalid(path)
    if spec.choices and raw not in spec.choices:
        raise _invalid(path)
    return raw


def _read_integer(spec: FieldSpec, raw: object, path: str) -> int:
    """Read a whole number within its bounds (a boolean is not one)."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _invalid(path)
    if spec.minimum is not None and raw < spec.minimum:
        raise _invalid(path)
    if spec.maximum is not None and raw > spec.maximum:
        raise _invalid(path)
    return raw


def _read_sealed(raw: object, path: str) -> Sealed:
    """Read a secret: only ``{"sealed": "<base64>"}``, never a clear-text value."""
    if not isinstance(raw, Mapping) or set(raw) != _SEALED_KEYS:
        raise _invalid(path)
    token = raw["sealed"]
    if not isinstance(token, str) or not token or not _is_base64(token):
        raise _invalid(path)
    return Sealed(token)


def _is_base64(text: str) -> bool:
    """Return whether ``text`` is strict base64."""
    try:
        base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _read_binary(raw: object, path: str) -> Binary:
    """Read a file: media type, bytes and the SHA-256 the document states, which must match."""
    if not isinstance(raw, Mapping) or set(raw) != _BINARY_KEYS:
        raise _invalid(path)
    media_type, digest, data = raw["media_type"], raw["sha256"], raw["data"]
    if not isinstance(media_type, str) or not media_type:
        raise _invalid(path)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise _invalid(path)
    if not isinstance(data, str):
        raise _invalid(path)
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid(path) from None
    binary = Binary(media_type, content)
    if binary.sha256 != digest:
        raise _invalid(path)
    return binary


def _read_strings(spec: FieldSpec, raw: object, path: str) -> list[Json]:
    """Read a list of texts."""
    if not isinstance(raw, list):
        raise _invalid(path)
    items: list[Json] = []
    for index, item in enumerate(raw):
        items.append(_read_text(spec, item, f"{path}[{index}]"))
    return items


def _read_objects(spec: FieldSpec, raw: object, path: str) -> list[Json]:
    """Read a list of objects, each by the item fields; defaults fill what an item leaves out."""
    if not isinstance(raw, list):
        raise _invalid(path)
    names = {item.name for item in spec.items}
    result: list[Json] = []
    for index, item in enumerate(raw):
        where = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            raise _invalid(where)
        for prop in item:
            if prop not in names:
                raise _invalid(f"{where}.{prop}", ErrorCode.CONFIGURATION_UNKNOWN_FIELD)
        read: dict[str, Json] = {}
        for sub in spec.items:
            if sub.name in item:
                read[sub.name] = _json(_read_value(sub, item[sub.name], f"{where}.{sub.name}"))
            elif sub.required:
                raise _invalid(f"{where}.{sub.name}")
            else:
                read[sub.name] = copy.deepcopy(sub.default)
        result.append(read)
    return result


def _json(value: Value) -> Json:
    """Return a value an item field holds, which is always plain JSON."""
    if isinstance(value, Sealed | Secret | Binary):
        raise TypeError("an item of a list holds no secret or file")
    return value


def to_json(document: Document) -> dict[str, Json]:
    """Write a document as JSON: what ``parse_document`` reads, without defaults added.

    Args:
        document: The document; any secret in it must be sealed.

    Raises:
        DomainError: ``invalid_configuration`` for a secret that is not sealed, with the path.
    """
    out: dict[str, Json] = {"format": document.format}
    if document.mode is not Mode.MERGE:
        out["mode"] = document.mode.value
    if document.scope.organisation is not None:
        out["scope"] = {"organisation": document.scope.organisation}
    if document.meta:
        out["meta"] = copy.deepcopy(dict(document.meta))
    if document.seal is not None:
        out["seal"] = copy.deepcopy(dict(document.seal))
    return out | _write_children(document.schema.root, document.root)


def _write_children(kind: Kind, entity: Entity) -> dict[str, Json]:
    """Write the collections an object holds, in the order the kind declares them."""
    out: dict[str, Json] = {}
    for coll in kind.collections:
        if coll.name in entity.children:
            out[coll.name] = _write_collection(coll, entity.children[coll.name])
    return out


def _write_collection(coll: Collection, entities: tuple[Entity, ...]) -> Json:
    """Write the objects of one collection as its shape says."""
    if coll.shape is Shape.SINGLE:
        return _write_entity(coll.kind, entities[0])
    if coll.shape is Shape.MAP:
        return _write_map(coll.kind, entities)
    if coll.shape is Shape.KEYS:
        return [entity.key[0] for entity in entities]
    return [_write_entity(coll.kind, entity) for entity in entities]


def _write_entity(kind: Kind, entity: Entity) -> dict[str, Json]:
    """Write one object: its key, its action unless it is an upsert, fields, collections."""
    out: dict[str, Json] = dict(zip(kind.key, entity.key, strict=True))
    if entity.action is not Action.UPSERT:
        out["action"] = entity.action.value
    for spec in kind.value_fields:
        if spec.name in entity.fields:
            out[spec.name] = _write_value(entity.fields[spec.name], f"{entity.path}.{spec.name}")
    return out | _write_children(kind, entity)


def _write_map(kind: Kind, entities: tuple[Entity, ...]) -> Json:
    """Write the entries of a map, nested once per key part."""
    leaf = kind.value_field
    root: dict[str, Json] = {}
    for entity in entities:
        level = root
        for part in entity.key[:-1]:
            nested = level.setdefault(part, {})
            assert isinstance(nested, dict)  # noqa: S101  # nosec B101 - built here, just above
            level = nested
        deleted = entity.action is Action.DELETE
        level[entity.key[-1]] = (
            None if deleted else _write_value(entity.fields[leaf.name], entity.path)
        )
    return root


def _write_value(value: Value, path: str) -> Json:
    """Write one value as JSON; a secret must be sealed, a file is written inline."""
    if isinstance(value, Secret):
        raise _invalid(path)
    if isinstance(value, Sealed):
        return {"sealed": value.token}
    if isinstance(value, Binary):
        return {
            "media_type": value.media_type,
            "sha256": value.sha256,
            "data": base64.b64encode(value.data).decode(),
        }
    return copy.deepcopy(value)
