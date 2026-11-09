"""The JSON Schema of a configuration document format (ADR 0099 point 10).

Generated from the format registry, so the published schema cannot drift from what the code
reads: ``python -m app.cli config-schema > docs/configuration-schema/v1.json`` writes it and a
test fails when the committed file differs. An editor with a JSON Schema plugin then completes
field names and flags a typo or a value out of bounds before the document reaches the planner,
which stays the authority (it also checks the rules a schema cannot say).
"""

from typing import cast

from app.domain.configuration_schema import (
    Collection,
    FieldSpec,
    FieldType,
    Format,
    Kind,
    Shape,
)
from app.domain.configuration_values import Json

DRAFT = "https://json-schema.org/draft/2020-12/schema"
ID = "https://openplanner.nl/schema/configuration/{version}.json"
"""Where the schema of a version is meant to be published; informational."""

_SEALED = {"oneOf": [{"$ref": "#/$defs/sealed"}, {"$ref": "#/$defs/plain"}]}
_ACTIONS = ["upsert", "create", "update", "delete"]


def json_schema(fmt: Format) -> dict[str, Json]:
    """Return the JSON Schema of ``fmt`` as a JSON document."""
    version = fmt.name.rsplit("/", 1)[-1]
    defs: dict[str, Json] = {
        "sealed": {
            "type": "object",
            "properties": {"sealed": {"type": "string", "minLength": 1}},
            "required": ["sealed"],
            "additionalProperties": False,
            "description": "A secret sealed under the document's passphrase.",
        },
        "plain": {
            "type": "object",
            "properties": {"plain": {"type": "string", "minLength": 1}},
            "required": ["plain"],
            "additionalProperties": False,
            "description": "A secret in clear text. OpenPlanner seals it when it stores it; keep "
            "the file as private as the secret.",
        },
        "binary": {
            "type": "object",
            "properties": {
                "media_type": {"type": "string", "minLength": 1},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "data": {"type": "string", "contentEncoding": "base64"},
            },
            "required": ["media_type", "sha256", "data"],
            "additionalProperties": False,
        },
    }
    for kind in fmt.kinds():
        if kind is not fmt.root:
            defs[kind.name] = _kind(kind)
    properties: dict[str, Json] = {
        "format": {"const": fmt.name},
        "mode": {"enum": ["merge", "replace"], "default": "merge"},
        "scope": {
            "oneOf": [
                {"const": "platform"},
                {
                    "type": "object",
                    "properties": {"organisation": {"type": "string", "minLength": 1}},
                    "required": ["organisation"],
                    "additionalProperties": False,
                },
            ],
            "default": "platform",
        },
        "meta": {"type": "object"},
        "seal": {"type": "object"},
    }
    for collection in fmt.root.collections:
        properties[collection.name] = _collection(collection)
    return {
        "$schema": DRAFT,
        "$id": ID.format(version=version),
        "title": f"OpenPlanner configuration document, {fmt.name}",
        "type": "object",
        "properties": dict(sorted(properties.items())),
        "required": ["format"],
        "additionalProperties": False,
        "$defs": dict(sorted(defs.items())),
    }


def _kind(kind: Kind) -> Json:
    """Return the definition of one kind: its key, fields, collections and ``action``."""
    properties: dict[str, Json] = {}
    for spec in kind.fields:
        properties[spec.name] = _field(spec)
    for collection in kind.collections:
        properties[collection.name] = _collection(collection)
    properties["action"] = {"enum": cast("list[Json]", _ACTIONS), "default": "upsert"}
    schema: dict[str, Json] = {
        "type": "object",
        "properties": dict(sorted(properties.items())),
        "additionalProperties": False,
    }
    if kind.key:
        schema["required"] = list(kind.key)
    if kind.description:
        schema["description"] = kind.description
    return schema


def _collection(collection: Collection) -> Json:
    """Return the schema of a collection as its shape writes it."""
    kind = collection.kind
    ref: dict[str, Json] = {"$ref": f"#/$defs/{kind.name}"}
    schema: dict[str, Json]
    if collection.shape is Shape.SINGLE:
        schema = ref
    elif collection.shape is Shape.KEYS:
        schema = {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}
    elif collection.shape is Shape.MAP:
        leaf = _field(kind.value_field)
        value: Json = {"oneOf": [leaf, {"type": "null"}]}
        for _ in kind.key:
            value = {"type": "object", "additionalProperties": value}
        schema = cast("dict[str, Json]", value)
    else:
        schema = {"type": "array", "items": ref}
    if collection.description:
        return (
            {**schema, "description": collection.description}
            if "$ref" not in schema
            else {
                "allOf": [schema],
                "description": collection.description,
            }
        )
    return schema


def _field(spec: FieldSpec) -> Json:
    """Return the schema of one field by its type and bounds."""
    schema: dict[str, Json]
    match spec.type:
        case FieldType.STRING:
            schema = {"type": "string"}
            if spec.max_length is not None:
                schema["maxLength"] = spec.max_length
            if spec.choices:
                schema["enum"] = list(spec.choices)
        case FieldType.INTEGER:
            schema = {"type": "integer"}
            if spec.minimum is not None:
                schema["minimum"] = spec.minimum
            if spec.maximum is not None:
                schema["maximum"] = spec.maximum
        case FieldType.BOOLEAN:
            schema = {"type": "boolean"}
        case FieldType.JSON:
            schema = {"type": "object"}
        case FieldType.SETTING:
            schema = {"type": ["string", "number", "boolean"]}
        case FieldType.SECRET:
            schema = dict(_SEALED)
        case FieldType.BINARY:
            schema = {"$ref": "#/$defs/binary"}
        case FieldType.STRINGS:
            schema = {"type": "array", "items": {"type": "string"}}
        case FieldType.OBJECTS:
            schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {item.name: _field(item) for item in spec.items},
                    "required": [item.name for item in spec.items if item.required],
                    "additionalProperties": False,
                },
            }
    if spec.nullable:
        schema = {"oneOf": [schema, {"type": "null"}]}
    if spec.description:
        schema = {"allOf": [schema], "description": spec.description}
    return schema
