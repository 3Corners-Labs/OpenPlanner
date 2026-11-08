"""The shape of a configuration document (ADR 0099): kinds, fields, collections, lookups."""

import pytest

from app.domain.authorization import Permission
from app.domain.configuration_schema import (
    Collection,
    FieldSpec,
    FieldType,
    Format,
    Kind,
    Shape,
    binary,
    boolean,
    integer,
    json_object,
    objects,
    secret,
    setting,
    strings,
    text,
)

NOTE = Kind(
    "note",
    key=("slug",),
    permission=Permission.GROUP_EDIT,
    tables=frozenset({"notes"}),
    fields=(text("slug", required=True, max_length=20), text("body", default="")),
)
ROOT = Kind(
    "installation",
    collections=(Collection("notes", NOTE, Shape.ENTITIES),),
)


def test_a_field_helper_sets_the_type_and_the_bounds() -> None:
    field = integer("slot_minutes", minimum=5, maximum=240, default=30)

    assert (field.type, field.minimum, field.maximum, field.default) == (
        FieldType.INTEGER,
        5,
        240,
        30,
    )
    assert not field.required
    assert boolean("enabled", default=False).type is FieldType.BOOLEAN
    assert strings("origins", ordered=True).ordered
    assert secret("password").type is FieldType.SECRET
    assert setting("value").type is FieldType.SETTING


def test_a_required_field_has_no_default_and_a_nullable_one_defaults_to_null() -> None:
    assert text("name", required=True).required
    assert not text("note", nullable=True).required
    assert text("note", nullable=True).default is None


def test_a_required_field_cannot_be_nullable_or_have_a_default() -> None:
    with pytest.raises(ValueError, match="required"):
        text("name", required=True, nullable=True)
    with pytest.raises(ValueError, match="required"):
        text("name", required=True, default="x")


def test_a_field_without_a_default_is_required_unless_it_may_be_null() -> None:
    assert text("name").required
    assert not text("name", nullable=True).required
    assert not text("name", default="").required


def test_a_list_of_objects_names_the_fields_of_its_items() -> None:
    rules = objects("rules", items=(integer("weekday", minimum=0, maximum=6, required=True),))

    assert rules.type is FieldType.OBJECTS
    assert [item.name for item in rules.items] == ["weekday"]
    with pytest.raises(ValueError, match="items"):
        FieldSpec("rules", FieldType.OBJECTS)


def test_the_items_of_a_list_hold_plain_values_only() -> None:
    with pytest.raises(ValueError, match="plain values"):
        objects("rules", items=(secret("password"),))
    with pytest.raises(ValueError, match="plain values"):
        objects("rules", items=(binary("logo"),))
    assert objects("rules", items=(json_object("labels"), strings("options"))).items


def test_a_format_finds_kinds_collections_and_fields_by_name() -> None:
    fmt = Format("test/v1", ROOT)

    assert fmt.kind("note") is NOTE
    assert fmt.kind("installation") is ROOT
    assert ROOT.collection("notes").kind is NOTE
    assert NOTE.field("body").default == ""
    assert [kind.name for kind in fmt.kinds()] == ["installation", "note"]
    assert fmt.tables() == frozenset({"notes"})
    with pytest.raises(KeyError):
        fmt.kind("missing")
    with pytest.raises(KeyError):
        NOTE.field("missing")
    with pytest.raises(KeyError):
        ROOT.collection("missing")


def test_a_kind_names_its_key_fields_among_its_fields() -> None:
    with pytest.raises(ValueError, match="key"):
        Kind("bad", key=("missing",), fields=(text("slug", required=True),))


def test_a_map_has_exactly_one_value_field_next_to_its_key_fields() -> None:
    entry = Kind(
        "entry",
        key=("locale", "key"),
        fields=(text("locale", required=True), text("key", required=True), text("text")),
    )

    assert entry.value_field.name == "text"
    bad = Kind("bad", key=("key",), fields=(text("key", required=True), text("a"), text("b")))
    with pytest.raises(ValueError, match="one value field"):
        _ = bad.value_field
