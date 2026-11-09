"""What an export leaves out for an actor who may not manage it (ADR 0099 point 11)."""

from app.domain.authorization import Permission, Target
from app.domain.configuration_visibility import visible_to
from tests.support.configuration import FORMAT, box, installation, shop


def allow_all(permission: Permission, target: Target) -> bool:
    return True


def deny(*denied: Permission):
    def may(permission: Permission, target: Target) -> bool:
        return permission not in denied

    return may


def tree():
    return installation(shop("s", box("b", size=7)))


def test_an_actor_who_may_manage_everything_sees_everything() -> None:
    root = tree()

    result = visible_to(root, FORMAT, allow_all)

    assert result.children["shops"] == root.children["shops"]


def test_a_field_that_needs_a_right_the_actor_lacks_is_left_out() -> None:
    result = visible_to(tree(), FORMAT, deny(Permission.SCHEDULE_MANAGE))

    only_box = result.children["shops"][0].children["boxes"][0]
    assert "size" not in only_box.fields
    assert "name" in only_box.fields


def test_an_object_the_actor_may_not_manage_is_left_out_with_what_it_holds() -> None:
    root = installation(clients=(shop("c"),))
    result = visible_to(
        installation(shop("s", box("b")), clients=root.children["clients"]),
        FORMAT,
        deny(Permission.API_CLIENTS_MANAGE),
    )

    assert "clients" not in result.children
    assert len(result.children["shops"]) == 1


def test_an_organisation_or_group_stays_as_the_frame_of_what_it_holds() -> None:
    result = visible_to(tree(), FORMAT, deny(Permission.ORGANISATION_MANAGE, Permission.GROUP_EDIT))

    kept_shop = result.children["shops"][0]
    assert kept_shop.key == ("s",)
    assert kept_shop.children["boxes"][0].key == ("b",)
    assert "name" not in kept_shop.fields
