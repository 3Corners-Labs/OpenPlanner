"""The JSON Schema generated from the format registry (ADR 0099 point 10)."""

import json
from pathlib import Path

from app.cli_config import render_schema
from app.domain.configuration_json_schema import json_schema
from app.domain.configuration_v1 import V1
from tests.support.configuration import FORMAT as MINI

ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = ROOT / "docs" / "configuration-schema" / "v1.json"


def test_the_published_schema_is_the_generated_one() -> None:
    assert PUBLISHED.read_text() == render_schema(), (
        "docs/configuration-schema/v1.json is out of date: run "
        "`python -m app.cli config-schema > docs/configuration-schema/v1.json`"
    )


def test_the_root_names_the_format_and_refuses_unknown_properties() -> None:
    schema = json_schema(V1)

    assert schema["properties"]["format"] == {"const": "openplanner.config/v1"}  # type: ignore[index]
    assert schema["required"] == ["format"]
    assert schema["additionalProperties"] is False
    assert {"platform", "organisations", "api_clients"} <= set(schema["properties"])  # type: ignore[arg-type]


def test_every_kind_has_a_definition_that_refuses_unknown_fields() -> None:
    defs = json_schema(V1)["$defs"]

    for kind in V1.kinds():
        if kind is V1.root:
            continue
        definition = defs[kind.name]  # type: ignore[index]
        assert definition["additionalProperties"] is False, kind.name
        assert "action" in definition["properties"], kind.name
        assert set(definition.get("required", ())) == set(kind.key), kind.name


def test_a_secret_is_a_sealed_object_and_a_file_a_binary() -> None:
    defs = json_schema(V1)["$defs"]

    assert defs["email_channel"]["properties"]["secret"]["oneOf"][0] == {"$ref": "#/$defs/secret"}  # type: ignore[index]
    assert defs["secret"]["oneOf"] == [  # type: ignore[index]
        {"$ref": "#/$defs/sealed"},
        {"$ref": "#/$defs/plain"},
    ]
    assert defs["plain"]["required"] == ["plain"]  # type: ignore[index]
    assert defs["organisation_logo"]["properties"]["data"] == {"$ref": "#/$defs/binary"}  # type: ignore[index]
    assert defs["sealed"]["required"] == ["sealed"]  # type: ignore[index]


def test_a_map_is_nested_once_per_key_part_with_null_for_removal() -> None:
    schema = json_schema(V1)
    texts = schema["$defs"]["group"]["properties"]["texts"]  # type: ignore[index]

    assert texts["type"] == "object"
    leaf = texts["additionalProperties"]["additionalProperties"]
    assert {"type": "null"} in leaf["oneOf"]


def test_bounds_and_choices_are_carried() -> None:
    group = json_schema(V1)["$defs"]["group"]["properties"]  # type: ignore[index]

    assert group["phone_mode"]["enum"] == ["hidden", "optional", "required"]
    assert group["slot_minutes"]["type"] == "integer"
    assert "minimum" in group["slot_minutes"]


def test_a_list_of_texts_a_key_list_and_a_single_object_have_their_shapes() -> None:
    schema = json_schema(V1)
    provider = schema["$defs"]["identity_provider"]["properties"]  # type: ignore[index]

    assert provider["domains"] == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
    }
    assert schema["properties"]["platform"]["allOf"] == [{"$ref": "#/$defs/platform"}]  # type: ignore[index]


def test_a_small_format_renders_every_field_type() -> None:
    schema = json_schema(MINI)
    box = schema["$defs"]["box"]["properties"]  # type: ignore[index]

    assert box["rules"]["items"]["required"] == ["day"]
    assert box["extra"] == {"type": "object"}
    assert box["note"]["oneOf"][1] == {"type": "null"}
    assert json.loads(json.dumps(schema)) == schema
