"""Reading and writing a configuration document (ADR 0099): strict, with natural keys."""

import base64
import copy
import hashlib

import pytest

from app.domain.configuration_document import (
    Document,
    Mode,
    Scope,
    parse_document,
    to_json,
)
from app.domain.configuration_schema import Collection, Format, Kind, Shape
from app.domain.configuration_tree import Action, path_of
from app.domain.configuration_values import Binary, Sealed, Secret
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import BOX, PLATFORM

ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", PLATFORM, Shape.SINGLE),
        Collection("boxes", BOX, Shape.ENTITIES),
    ),
)
FORMAT = Format("test/v1", ROOT)
PNG = b"\x89PNG\r\n-bytes"


def base() -> dict[str, object]:
    return {"format": "test/v1"}


def parse(data: dict[str, object]) -> Document:
    return parse_document(data, {FORMAT.name: FORMAT})


def refused(data: object, code: ErrorCode, field: str | None = None) -> None:
    with pytest.raises(DomainError) as error:
        parse_document(data, {FORMAT.name: FORMAT})
    assert error.value.code is code
    if field is not None:
        assert error.value.field == field


def test_an_empty_document_has_the_defaults_of_merge_in_the_platform_scope() -> None:
    document = parse(base())

    assert document.format == "test/v1"
    assert document.mode is Mode.MERGE
    assert document.scope == Scope()
    assert document.scope.is_platform
    assert document.root.children == {}
    assert len(document.digest) == 64


def test_the_format_must_be_a_known_version() -> None:
    refused({}, ErrorCode.CONFIGURATION_FORMAT_UNSUPPORTED)
    refused({"format": "other/v9"}, ErrorCode.CONFIGURATION_FORMAT_UNSUPPORTED)
    refused({"format": 1}, ErrorCode.CONFIGURATION_FORMAT_UNSUPPORTED)


def test_a_document_that_is_not_an_object_is_refused() -> None:
    refused([], ErrorCode.INVALID_CONFIGURATION)
    refused("text", ErrorCode.INVALID_CONFIGURATION)
    refused(None, ErrorCode.INVALID_CONFIGURATION)


def test_mode_and_scope_are_read_and_checked() -> None:
    replaced = parse({**base(), "mode": "replace", "scope": {"organisation": "acme"}})

    assert replaced.mode is Mode.REPLACE
    assert replaced.scope == Scope("acme")
    assert not replaced.scope.is_platform
    assert parse({**base(), "scope": "platform"}).scope.is_platform
    refused({**base(), "mode": "wipe"}, ErrorCode.INVALID_CONFIGURATION, "mode")
    refused({**base(), "scope": "everything"}, ErrorCode.INVALID_CONFIGURATION, "scope")
    refused({**base(), "scope": {"organisation": ""}}, ErrorCode.INVALID_CONFIGURATION, "scope")
    refused(
        {**base(), "scope": {"group": "x"}}, ErrorCode.CONFIGURATION_UNKNOWN_FIELD, "scope.group"
    )


def test_an_unknown_top_level_property_is_refused_with_its_path() -> None:
    refused({**base(), "organisations": []}, ErrorCode.CONFIGURATION_UNKNOWN_FIELD, "organisations")


def test_an_unknown_field_is_refused_with_the_path_through_natural_keys() -> None:
    data = {**base(), "boxes": [{"slug": "a", "name": "A", "colur": "red"}]}

    refused(data, ErrorCode.CONFIGURATION_UNKNOWN_FIELD, "boxes[a].colur")


def test_an_unknown_field_inside_a_list_item_is_refused_with_its_index() -> None:
    data = {**base(), "boxes": [{"slug": "a", "name": "A", "rules": [{"day": 1, "x": 1}]}]}

    refused(data, ErrorCode.CONFIGURATION_UNKNOWN_FIELD, "boxes[a].rules[0].x")


def test_a_box_is_read_with_its_natural_key_action_and_present_fields_only() -> None:
    document = parse({**base(), "boxes": [{"slug": "a", "name": "A", "size": 3}]})

    (box,) = document.root.children["boxes"]
    assert (box.kind, box.key, box.action) == ("box", ("a",), Action.UPSERT)
    assert box.fields == {"name": "A", "size": 3}
    assert box.path == "boxes[a]"


def test_an_action_is_read_per_object() -> None:
    data = {**base(), "boxes": [{"slug": "a", "action": "delete"}, {"slug": "b", "name": "B"}]}

    first, second = parse(data).root.children["boxes"]

    assert first.action is Action.DELETE
    assert second.action is Action.UPSERT
    refused(
        {**base(), "boxes": [{"slug": "a", "action": "purge"}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[a].action",
    )


def test_the_natural_key_is_required_and_unique_within_a_collection() -> None:
    refused({**base(), "boxes": [{"name": "A"}]}, ErrorCode.INVALID_CONFIGURATION, "boxes[0].slug")
    refused(
        {**base(), "boxes": [{"slug": "", "name": "A"}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[0].slug",
    )
    refused(
        {**base(), "boxes": [{"slug": "a"}, {"slug": "a"}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[a]",
    )
    refused({**base(), "boxes": {"a": {}}}, ErrorCode.INVALID_CONFIGURATION, "boxes")
    refused({**base(), "boxes": ["a"]}, ErrorCode.INVALID_CONFIGURATION, "boxes[0]")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 5),
        ("name", None),
        ("size", "3"),
        ("size", True),
        ("size", 0),
        ("size", 10),
        ("colour", "green"),
        ("slug", "x" * 21),
        ("extra", []),
        ("tags", "a"),
        ("tags", [1]),
        ("order", "a"),
        ("order", [1]),
        ("rules", {}),
        ("rules", ["x"]),
        ("rules", [{"label": "x"}]),
        ("note", 4),
    ],
)
def test_a_value_of_the_wrong_type_or_outside_its_bounds_is_refused(
    field: str, value: object
) -> None:
    box: dict[str, object] = {"slug": "a", "name": "A", field: value}
    data = {**base(), "boxes": [box]}

    with pytest.raises(DomainError) as error:
        parse(data)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert str(error.value.field).startswith("boxes[")


def test_null_is_a_value_only_of_a_nullable_field() -> None:
    document = parse({**base(), "boxes": [{"slug": "a", "note": None}]})

    (box,) = document.root.children["boxes"]
    assert box.fields == {"note": None}


def test_a_list_of_objects_is_read_with_the_defaults_of_its_items_filled_in() -> None:
    data = {**base(), "boxes": [{"slug": "a", "rules": [{"day": 1}, {"day": 2, "label": "x"}]}]}

    (box,) = parse(data).root.children["boxes"]

    assert box.fields["rules"] == [{"day": 1, "label": None}, {"day": 2, "label": "x"}]


def test_a_secret_is_only_ever_a_sealed_value() -> None:
    sealed = base() | {"boxes": [{"slug": "a", "password": {"sealed": "c2VhbGVk"}}]}

    (box,) = parse(sealed).root.children["boxes"]
    assert box.fields["password"] == Sealed("c2VhbGVk")
    for bad in (
        "hunter2",
        {"sealed": ""},
        {"sealed": "not base64!"},
        {"sealed": "YQ==", "x": 1},
        {"sealed": "YQ==", "plain": "x"},
        {"plain": ""},
        {"plain": 5},
        {"plain": "x", "x": 1},
        5,
    ):
        refused(
            {**base(), "boxes": [{"slug": "a", "password": bad}]},
            ErrorCode.INVALID_CONFIGURATION,
            "boxes[a].password",
        )


def test_a_secret_may_be_written_in_clear_text_as_plain() -> None:
    data = base() | {"boxes": [{"slug": "a", "password": {"plain": "hunter2"}}]}

    (box,) = parse(data).root.children["boxes"]

    assert box.fields["password"] == Secret("hunter2")


def test_a_clear_text_secret_is_never_in_the_message_of_a_refusal() -> None:
    data = base() | {"boxes": [{"slug": "a", "password": {"plain": "hunter2", "x": 1}}]}

    with pytest.raises(DomainError) as error:
        parse(data)

    assert "hunter2" not in repr(error.value) + str(error.value.field)


def test_a_binary_carries_its_hash_and_a_wrong_hash_is_refused() -> None:
    good = {
        "media_type": "image/png",
        "sha256": hashlib.sha256(PNG).hexdigest(),
        "data": base64.b64encode(PNG).decode(),
    }
    data = {**base(), "boxes": [{"slug": "a", "logo": {"data": good}}]}

    (box,) = parse(data).root.children["boxes"]
    (logo,) = box.children["logo"]
    assert logo.fields["data"] == Binary("image/png", PNG)
    assert logo.key == ()
    for bad in (
        {**good, "sha256": "0" * 64},
        {**good, "data": "***"},
        {**good, "media_type": ""},
        {"media_type": "image/png", "data": good["data"]},
        {**good, "extra": 1},
        "bytes",
    ):
        refused(
            {**base(), "boxes": [{"slug": "a", "logo": {"data": bad}}]},
            ErrorCode.INVALID_CONFIGURATION,
            "boxes[a].logo.data",
        )


def test_a_map_is_read_as_objects_keyed_by_its_key_parts() -> None:
    data = {
        **base(),
        "boxes": [
            {
                "slug": "a",
                "texts": {"nl": {"k.one": "Een", "k.two": "Twee"}, "en": {"k.one": "One"}},
            }
        ],
    }

    (box,) = parse(data).root.children["boxes"]
    entries = box.children["texts"]

    assert [(e.key, e.fields["text"], e.action) for e in entries] == [
        (("nl", "k.one"), "Een", Action.UPSERT),
        (("nl", "k.two"), "Twee", Action.UPSERT),
        (("en", "k.one"), "One", Action.UPSERT),
    ]
    assert entries[0].path == "boxes[a].texts[nl][k.one]"


def test_a_null_in_a_map_removes_the_entry_and_a_wrong_nesting_is_refused() -> None:
    data = {**base(), "boxes": [{"slug": "a", "texts": {"nl": {"k": None}}}]}

    (box,) = parse(data).root.children["boxes"]
    (entry,) = box.children["texts"]
    assert (entry.action, entry.fields) == (Action.DELETE, {})
    refused(
        {**base(), "boxes": [{"slug": "a", "texts": {"nl": "text"}}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[a].texts[nl]",
    )
    refused(
        {**base(), "boxes": [{"slug": "a", "texts": ["x"]}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[a].texts",
    )
    refused(
        {**base(), "boxes": [{"slug": "a", "texts": {"nl": {"k": 5}}}]},
        ErrorCode.INVALID_CONFIGURATION,
        "boxes[a].texts[nl][k]",
    )


def test_a_settings_map_takes_scalars_of_any_type() -> None:
    data = {**base(), "platform": {"settings": {"limit": 5, "name": "x", "on": True, "ratio": 0.5}}}

    (platform,) = parse(data).root.children["platform"]
    settings = {e.key: e.fields["value"] for e in platform.children["settings"]}

    assert settings == {("limit",): 5, ("name",): "x", ("on",): True, ("ratio",): 0.5}
    assert platform.key == ()
    refused(
        {**base(), "platform": {"settings": {"limit": [1]}}},
        ErrorCode.INVALID_CONFIGURATION,
        "platform.settings[limit]",
    )


def test_a_single_object_is_read_as_the_only_object_of_its_kind() -> None:
    data = {**base(), "platform": {"switch": {"enabled": True}}}

    (platform,) = parse(data).root.children["platform"]
    (switch,) = platform.children["switch"]

    assert switch.fields == {"enabled": True}
    assert switch.path == "platform.switch"
    refused(
        {**base(), "platform": {"switch": [{"enabled": True}]}},
        ErrorCode.INVALID_CONFIGURATION,
        "platform.switch",
    )


def test_a_child_collection_of_entities_is_read_under_its_parent() -> None:
    data = {
        **base(),
        "boxes": [{"slug": "a", "labels": [{"name": "L1", "weight": 2}, {"name": "L2"}]}],
    }

    (box,) = parse(data).root.children["boxes"]
    first, second = box.children["labels"]

    assert (first.key, first.fields, first.path) == (("L1",), {"weight": 2}, "boxes[a].labels[L1]")
    assert (second.key, second.fields) == (("L2",), {})


def test_a_collection_the_document_names_empty_is_still_named() -> None:
    document = parse({**base(), "boxes": [{"slug": "a", "labels": []}]})

    (box,) = document.root.children["boxes"]
    assert box.children == {"labels": ()}
    assert parse({**base(), "boxes": []}).root.children == {"boxes": ()}


def test_the_digest_is_the_same_for_the_same_content_in_any_order() -> None:
    one = parse({**base(), "boxes": [{"slug": "a", "name": "A", "size": 2}]})
    two = parse({"boxes": [{"size": 2, "name": "A", "slug": "a"}], "format": "test/v1"})
    other = parse({**base(), "boxes": [{"slug": "a", "name": "A", "size": 3}]})

    assert one.digest == two.digest != other.digest


def test_to_json_writes_back_what_was_read_without_adding_defaults() -> None:
    data = {
        "format": "test/v1",
        "mode": "replace",
        "scope": {"organisation": "acme"},
        "boxes": [
            {
                "slug": "a",
                "action": "update",
                "name": "A",
                "size": 3,
                "note": None,
                "tags": ["x", "y"],
                "order": ["p", "q"],
                "rules": [{"day": 1, "label": None}],
                "password": {"sealed": "c2VhbGVk"},
                "labels": [{"name": "L1", "weight": 2}],
                "texts": {"nl": {"k": "Een"}},
                "logo": {
                    "data": {
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(PNG).hexdigest(),
                        "data": base64.b64encode(PNG).decode(),
                    }
                },
            }
        ],
        "platform": {"settings": {"limit": 5}, "switch": {"enabled": True}},
    }

    written = to_json(parse(copy.deepcopy(data)))

    assert written == data


def test_to_json_leaves_out_the_defaults_of_the_header() -> None:
    assert to_json(parse(base())) == {"format": "test/v1"}


def test_to_json_writes_a_deleted_map_entry_as_null() -> None:
    data = {**base(), "boxes": [{"slug": "a", "texts": {"nl": {"k": None}}}]}

    assert to_json(parse(data)) == data


def test_to_json_refuses_a_secret_that_is_not_sealed() -> None:
    from dataclasses import replace

    from app.domain.configuration_values import Secret

    document = parse({**base(), "boxes": [{"slug": "a", "password": {"sealed": "YQ=="}}]})
    (box,) = document.root.children["boxes"]
    opened = replace(box, fields={"password": Secret("plain")})
    bad = replace(document, root=replace(document.root, children={"boxes": (opened,)}))

    with pytest.raises(DomainError) as error:
        to_json(bad)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert error.value.field == "boxes[a].password"


def test_to_json_writes_a_secret_as_plain_only_when_asked() -> None:
    from dataclasses import replace

    document = parse({**base(), "boxes": [{"slug": "a", "password": {"sealed": "YQ=="}}]})
    (box,) = document.root.children["boxes"]
    opened = replace(box, fields={"password": Secret("plain")})
    opened_document = replace(document, root=replace(document.root, children={"boxes": (opened,)}))

    written = to_json(opened_document, plain_secrets=True)

    assert written["boxes"] == [{"slug": "a", "password": {"plain": "plain"}}]
    assert parse(written).root.children["boxes"][0].fields["password"] == Secret("plain")


def test_a_path_names_the_collection_and_the_key_parts() -> None:
    assert path_of("", "boxes", ("a",)) == "boxes[a]"
    assert path_of("boxes[a]", "labels", ("L1",)) == "boxes[a].labels[L1]"
    assert path_of("boxes[a]", "texts", ("nl", "k")) == "boxes[a].texts[nl][k]"
    assert path_of("", "platform", ()) == "platform"
    assert path_of("platform", "switch", ()) == "platform.switch"


def test_a_key_part_of_a_map_is_checked_against_its_field() -> None:
    refused(
        {**base(), "boxes": [{"slug": "a", "texts": {"x" * 9: {"k": "x"}}}]},
        ErrorCode.INVALID_CONFIGURATION,
    )


def test_a_natural_key_is_trimmed_like_every_text() -> None:
    document = parse(
        {**base(), "boxes": [{"slug": " a ", "texts": {" nl ": {" k ": "x"}}, "tags": [" t "]}]}
    )

    (box,) = document.root.children["boxes"]
    assert box.key == ("a",)
    assert box.children["texts"][0].key == ("nl", "k")
    assert box.children["tags"][0].key == ("t",)
    refused(
        {**base(), "boxes": [{"slug": "   "}]}, ErrorCode.INVALID_CONFIGURATION, "boxes[0].slug"
    )
    refused(
        {**base(), "boxes": [{"slug": "a", "tags": ["x", " x "]}]}, ErrorCode.INVALID_CONFIGURATION
    )
