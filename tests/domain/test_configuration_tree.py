"""The walks over the configuration tree (ADR 0099 point 6)."""

from app.domain.configuration_tree import Entity, any_field, drop_fields, map_fields, walk


def tree() -> Entity:
    group = Entity("group", ("helpdesk",), fields={"name": "Help", "secret": 7})
    organisation = Entity(
        "organisation", ("acme",), fields={"name": "Acme"}, children={"groups": (group,)}
    )
    return Entity("installation", children={"organisations": (organisation,)})


def test_map_fields_gives_every_field_its_kind_and_address() -> None:
    seen: list[tuple[str, tuple[str, ...], str]] = []

    def record(kind: str, address: tuple[str, ...], name: str, value: object) -> object:
        seen.append((kind, address, name))
        return f"{value}!"

    mapped = map_fields(tree(), record)  # type: ignore[arg-type]

    assert seen == [
        ("organisation", ("acme",), "name"),
        ("group", ("acme", "helpdesk"), "name"),
        ("group", ("acme", "helpdesk"), "secret"),
    ]
    group = dict(walk(mapped, "group"))[("acme", "helpdesk")]
    assert group.fields == {"name": "Help!", "secret": "7!"}


def test_any_field_looks_below_the_entity() -> None:
    assert any_field(tree(), lambda value: value == 7)
    assert not any_field(tree(), lambda value: value == 8)
    assert any_field(Entity("platform", fields={"a": 1}), lambda value: value == 1)


def test_drop_fields_removes_the_matching_fields_everywhere() -> None:
    dropped = drop_fields(tree(), lambda value: isinstance(value, int))

    group = dict(walk(dropped, "group"))[("acme", "helpdesk")]
    assert group.fields == {"name": "Help"}
    assert not any_field(dropped, lambda value: isinstance(value, int))
