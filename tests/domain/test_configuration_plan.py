"""Planning a configuration document (ADR 0099 points 4-7, 11): actions, modes, scope, rights."""

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import replace

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import (
    ChangeAction,
    Refusal,
    Resolution,
    RuleContext,
    Usage,
)
from app.domain.configuration_tree import walk
from app.domain.configuration_values import Binary, Secret, Value
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import (
    BOX,
    Recorder,
    box,
    build_format,
    entity,
    format_with_box,
    installation,
    plan,
    plan_with_values,
    refusals,
    shop,
)

B = ("s", "b")


def kinds(result: object) -> list[tuple[str, tuple[str, ...], ChangeAction]]:
    return [(c.kind, c.address, c.action) for c in result.changes]  # type: ignore[attr-defined]


def test_an_empty_document_changes_nothing() -> None:
    result = plan({})

    assert result.changes == ()
    assert result.refusals == ()
    assert result.ok


def test_a_document_that_states_what_exists_changes_nothing() -> None:
    result = plan(
        {"shops": [{"slug": "s", "name": "Shop", "boxes": [{"slug": "b", "name": "Box"}]}]}
    )

    assert result.changes == ()
    assert result.ok


def test_a_new_object_is_created_with_its_defaults_and_names_only_what_the_document_gave() -> None:
    result = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n", "name": "New", "size": 3}]}]})

    (change,) = result.changes
    assert (change.kind, change.key, change.address, change.action) == (
        "box",
        ("n",),
        ("s", "n"),
        ChangeAction.CREATE,
    )
    assert change.fields == ("name", "size")
    assert change.values == {
        "name": "New",
        "size": 3,
        "colour": "red",
        "note": None,
        "extra": {},
        "order": [],
        "rules": [],
    }
    assert change.path == "shops[s].boxes[n]"
    assert (change.organisation, change.group) == ("s", "n")
    assert change.permissions == {Permission.GROUP_CREATE, Permission.SCHEDULE_MANAGE}


def test_a_new_object_must_state_what_has_no_default() -> None:
    result = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n"}]}]})

    assert refusals(result) == [("shops[s].boxes[n]", ErrorCode.INVALID_CONFIGURATION, "name")]
    assert not result.ok


def test_an_update_names_only_the_fields_that_change() -> None:
    data = {"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Other", "colour": "red"}]}]}

    (change,) = plan(data).changes

    assert (change.action, change.fields) == (ChangeAction.UPDATE, ("name",))
    assert change.values["name"] == "Other"
    assert change.values["size"] == 5
    assert change.permissions == {Permission.GROUP_EDIT}


def test_a_field_with_its_own_permission_asks_for_that_permission() -> None:
    data = {"shops": [{"slug": "s", "boxes": [{"slug": "b", "size": 7}]}]}

    (change,) = plan(data).changes

    assert change.fields == ("size",)
    assert change.permissions == {Permission.SCHEDULE_MANAGE}


def test_null_sets_a_nullable_field_and_a_change_is_only_a_difference() -> None:
    current = installation(shop("s", box("b", note="x")))

    (change,) = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "note": None}]}]}, current
    ).changes
    assert change.fields == ("note",)
    assert change.values["note"] is None
    assert (
        plan({"shops": [{"slug": "s", "boxes": [{"slug": "b", "note": "x"}]}]}, current).changes
        == ()
    )


def test_create_refuses_an_object_that_exists_and_update_one_that_does_not() -> None:
    created = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "action": "create", "name": "X"}]}]}
    )
    updated = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n", "action": "update"}]}]})

    assert refusals(created) == [("shops[s].boxes[b]", ErrorCode.CONFIGURATION_OBJECT_EXISTS, None)]
    assert refusals(updated) == [
        ("shops[s].boxes[n]", ErrorCode.CONFIGURATION_OBJECT_MISSING, None)
    ]


def test_create_and_update_work_when_they_should() -> None:
    created = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "n", "action": "create", "name": "X"}]}]}
    )
    updated = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "action": "update", "name": "Y"}]}]}
    )

    assert kinds(created) == [("box", ("s", "n"), ChangeAction.CREATE)]
    assert kinds(updated) == [("box", ("s", "b"), ChangeAction.UPDATE)]


def test_delete_removes_what_exists_and_is_nothing_when_it_is_already_gone() -> None:
    gone = plan({"shops": [{"slug": "s", "boxes": [{"slug": "b", "action": "delete"}]}]})
    absent = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n", "action": "delete"}]}]})

    (change,) = gone.changes
    assert (change.action, change.permissions) == (ChangeAction.DELETE, {Permission.GROUP_DELETE})
    assert absent.changes == ()
    assert absent.ok


def test_a_document_cannot_name_children_of_an_object_it_deletes() -> None:
    data = {
        "shops": [
            {
                "slug": "s",
                "boxes": [{"slug": "b", "action": "delete", "labels": [{"name": "L"}]}],
            }
        ]
    }

    assert refusals(plan(data)) == [
        ("shops[s].boxes[b].labels", ErrorCode.INVALID_CONFIGURATION, None)
    ]


def test_children_of_a_created_object_are_created_after_it() -> None:
    data = {
        "shops": [
            {
                "slug": "s",
                "boxes": [
                    {
                        "slug": "n",
                        "name": "New",
                        "labels": [{"name": "L1", "weight": 2}, {"name": "L2"}],
                        "tags": ["a"],
                        "texts": {"nl": {"k": "Een"}},
                        "logo": {
                            "data": {
                                "media_type": "image/png",
                                "sha256": hashlib.sha256(b"x").hexdigest(),
                                "data": "eA==",
                            }
                        },
                    }
                ],
            }
        ]
    }

    result = plan(data)

    assert kinds(result) == [
        ("box", ("s", "n"), ChangeAction.CREATE),
        ("label", ("s", "n", "L1"), ChangeAction.CREATE),
        ("label", ("s", "n", "L2"), ChangeAction.CREATE),
        ("entry", ("s", "n", "nl", "k"), ChangeAction.CREATE),
        ("logo", ("s", "n"), ChangeAction.CREATE),
        ("tag", ("s", "n", "a"), ChangeAction.CREATE),
    ]
    assert result.ok


def test_a_child_cannot_be_updated_below_a_parent_that_does_not_exist() -> None:
    data = {
        "shops": [
            {
                "slug": "s",
                "boxes": [
                    {"slug": "n", "name": "N", "labels": [{"name": "L", "action": "update"}]}
                ],
            }
        ]
    }

    assert refusals(plan(data)) == [
        ("shops[s].boxes[n].labels[L]", ErrorCode.CONFIGURATION_OBJECT_MISSING, None)
    ]


def test_an_unchanged_parent_still_passes_on_the_changes_of_its_children() -> None:
    data = {
        "shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Box", "labels": [{"name": "L"}]}]}]
    }

    result = plan(data)

    assert kinds(result) == [("label", ("s", "b", "L"), ChangeAction.CREATE)]


def test_merge_leaves_what_the_document_does_not_name_and_replace_deletes_it() -> None:
    current = installation(shop("s", box("a"), box("b"), box("c")), shop("t", box("z")))
    data = {"shops": [{"slug": "s", "boxes": [{"slug": "a"}]}]}

    merged = plan(data, current)
    replaced = plan({"mode": "replace", **data}, current)

    assert merged.changes == ()
    assert kinds(replaced) == [
        ("box", ("s", "b"), ChangeAction.DELETE),
        ("box", ("s", "c"), ChangeAction.DELETE),
        ("shop", ("t",), ChangeAction.DELETE),
    ]


def test_replace_only_touches_collections_the_document_names() -> None:
    current = installation(
        shop("s", box("a", _children={"labels": (entity("label", ("L",), {"weight": 1}),)}))
    )

    unnamed = plan({"mode": "replace", "shops": [{"slug": "s", "boxes": [{"slug": "a"}]}]}, current)
    emptied = plan(
        {"mode": "replace", "shops": [{"slug": "s", "boxes": [{"slug": "a", "labels": []}]}]},
        current,
    )

    assert unnamed.changes == ()
    assert kinds(emptied) == [("label", ("s", "a", "L"), ChangeAction.DELETE)]


def test_replace_on_the_top_level_deletes_unlisted_shops() -> None:
    current = installation(shop("s"), shop("t"))

    result = plan({"mode": "replace", "shops": [{"slug": "s"}]}, current)

    assert kinds(result) == [("shop", ("t",), ChangeAction.DELETE)]


def test_replace_does_not_delete_the_single_object_of_a_kind() -> None:
    current = installation(
        shop(
            "s",
            box(
                "a", _children={"logo": (entity("logo", (), {"data": Binary("image/png", b"x")}),)}
            ),
        )
    )

    result = plan({"mode": "replace", "shops": [{"slug": "s", "boxes": [{"slug": "a"}]}]}, current)

    assert result.changes == ()


def test_a_map_entry_set_to_null_is_deleted_and_replace_drops_unlisted_entries() -> None:
    texts = (
        entity("entry", ("nl", "k1"), {"text": "Een"}),
        entity("entry", ("nl", "k2"), {"text": "Twee"}),
    )
    current = installation(shop("s", box("a", _children={"texts": texts})))
    box_doc = {"slug": "a", "texts": {"nl": {"k1": None}}}

    merged = plan({"shops": [{"slug": "s", "boxes": [box_doc]}]}, current)
    replaced = plan(
        {
            "mode": "replace",
            "shops": [{"slug": "s", "boxes": [{"slug": "a", "texts": {"nl": {"k1": "Een"}}}]}],
        },
        current,
    )

    assert kinds(merged) == [("entry", ("s", "a", "nl", "k1"), ChangeAction.DELETE)]
    assert merged.changes[0].permissions == {Permission.TEXTS_MANAGE}
    assert kinds(replaced) == [("entry", ("s", "a", "nl", "k2"), ChangeAction.DELETE)]


def test_a_map_entry_changes_when_its_value_does() -> None:
    texts = (entity("entry", ("nl", "k1"), {"text": "Een"}),)
    current = installation(shop("s", box("a", _children={"texts": texts})))

    same = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "a", "texts": {"nl": {"k1": "Een"}}}]}]},
        current,
    )
    other = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "a", "texts": {"nl": {"k1": "Eén"}}}]}]},
        current,
    )

    assert same.changes == ()
    (change,) = other.changes
    assert (change.action, change.fields, change.values["text"]) == (
        ChangeAction.UPDATE,
        ("text",),
        "Eén",
    )


def test_keys_are_created_and_replace_removes_the_unlisted() -> None:
    tags = (entity("tag", ("a",)), entity("tag", ("b",)))
    current = installation(shop("s", box("x", _children={"tags": tags})))

    added = plan({"shops": [{"slug": "s", "boxes": [{"slug": "x", "tags": ["a", "c"]}]}]}, current)
    replaced = plan(
        {"mode": "replace", "shops": [{"slug": "s", "boxes": [{"slug": "x", "tags": ["a", "c"]}]}]},
        current,
    )

    assert kinds(added) == [("tag", ("s", "x", "c"), ChangeAction.CREATE)]
    assert kinds(replaced) == [
        ("tag", ("s", "x", "c"), ChangeAction.CREATE),
        ("tag", ("s", "x", "b"), ChangeAction.DELETE),
    ]


def test_a_single_object_is_created_updated_and_deleted() -> None:
    logo = entity("logo", (), {"data": Binary("image/png", b"old")})
    current = installation(shop("s", box("a", _children={"logo": (logo,)})))
    new = Binary("image/png", b"new")

    def doc(logo_doc: dict[str, object]) -> dict[str, object]:
        return {"shops": [{"slug": "s", "boxes": [{"slug": "a", "logo": logo_doc}]}]}

    gone = plan(doc({"action": "delete"}), current)
    created = plan(doc({"data": _binary(b"new")}), installation(shop("s", box("a"))))
    updated = plan(doc({"data": _binary(b"new")}), current)
    same = plan(doc({"data": _binary(b"old")}), current)

    assert kinds(gone) == [("logo", ("s", "a"), ChangeAction.DELETE)]
    assert kinds(created) == [("logo", ("s", "a"), ChangeAction.CREATE)]
    assert kinds(updated) == [("logo", ("s", "a"), ChangeAction.UPDATE)]
    assert updated.changes[0].values["data"] == new
    assert same.changes == ()


def _binary(data: bytes) -> dict[str, str]:
    return {
        "media_type": "image/png",
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode(),
    }


def test_the_platform_cannot_be_deleted_but_its_children_can_change() -> None:
    gone = plan({"platform": {"action": "delete"}})
    changed = plan({"platform": {"switch": {"enabled": True}, "settings": {"limit": 3}}})

    assert refusals(gone) == [("platform", ErrorCode.INVALID_CONFIGURATION, None)]
    assert kinds(changed) == [
        ("setting", ("limit",), ChangeAction.CREATE),
        ("switch", (), ChangeAction.CREATE),
    ]


def test_lists_are_compared_as_sets_unless_their_order_matters() -> None:
    current = installation(
        shop(
            "s",
            box(
                "b",
                order=["x", "y"],
                rules=[{"day": 1, "label": None}, {"day": 2, "label": "a"}],
            ),
        )
    )

    def doc(**fields: object) -> dict[str, object]:
        return {"shops": [{"slug": "s", "boxes": [{"slug": "b", **fields}]}]}

    shuffled = plan(doc(rules=[{"day": 2, "label": "a"}, {"day": 1}]), current)
    reordered = plan(doc(order=["y", "x"]), current)

    assert shuffled.changes == ()
    (change,) = reordered.changes
    assert change.fields == ("order",)


def test_a_secret_that_is_the_same_is_no_change_and_one_that_differs_never_shows() -> None:
    current = installation(shop("s", box("b", password=Secret("old-secret"))))

    same = plan_with_values(current, password=Secret("old-secret"))
    other = plan_with_values(current, password=Secret("new-secret"))

    assert same.changes == ()
    (change,) = other.changes
    assert change.fields == ("password",)
    assert "new-secret" not in repr(change)
    assert "new-secret" not in repr(other)
    assert all("secret" not in line for line in other.lines())


def test_a_secret_the_document_leaves_out_is_kept() -> None:
    current = installation(shop("s", box("b", password=Secret("kept"))))

    result = plan({"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Other"}]}]}, current)

    (change,) = result.changes
    assert change.fields == ("name",)
    assert change.values["password"] == Secret("kept")


def test_each_change_is_checked_against_the_permission_of_its_own_screen() -> None:
    recorder = Recorder()
    data = {
        "shops": [
            {
                "slug": "s",
                "name": "Renamed",
                "boxes": [
                    {
                        "slug": "n",
                        "name": "N",
                        "texts": {"nl": {"k": "x"}},
                        "logo": {"data": _binary(b"png")},
                    }
                ],
            }
        ],
        "clients": [{"client_id": "c", "organisation": "s"}],
    }

    plan(data, installation(shop("s")), may_change=recorder)

    asked = set(recorder.asked)
    assert (Permission.ORGANISATION_MANAGE, Target.everything()) in asked
    assert (Permission.GROUP_CREATE, Target.for_organisation("s")) in asked
    assert (Permission.TEXTS_MANAGE, Target.for_group("n", "s")) in asked
    assert (Permission.BRANDING_MANAGE, Target.for_organisation("s")) in asked
    assert (Permission.API_CLIENTS_MANAGE, Target.everything()) in asked


def test_a_change_the_actor_may_not_make_is_a_refusal_not_a_silent_skip() -> None:
    data = {
        "shops": [
            {"slug": "s", "boxes": [{"slug": "b", "name": "Other"}, {"slug": "c", "name": "C"}]}
        ]
    }

    result = plan(data, may_change=Recorder(Permission.GROUP_EDIT))

    assert refusals(result) == [("shops[s].boxes[b]", ErrorCode.NOT_PERMITTED, None)]
    assert not result.ok


def test_deleting_asks_for_the_delete_permission() -> None:
    recorder = Recorder(Permission.GROUP_DELETE)

    result = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "action": "delete"}]}]},
        may_change=recorder,
    )

    assert refusals(result) == [("shops[s].boxes[b]", ErrorCode.NOT_PERMITTED, None)]
    assert recorder.asked == [(Permission.GROUP_DELETE, Target.for_organisation("s"))]


def test_a_platform_document_may_name_anything() -> None:
    data = {
        "platform": {"settings": {"a": 1}},
        "shops": [{"slug": "t", "name": "T"}],
        "clients": [{"client_id": "c", "organisation": "t"}],
    }

    assert plan(data).ok


def test_an_organisation_document_may_name_its_own_organisation_and_its_clients() -> None:
    data = {
        "scope": {"organisation": "s"},
        "shops": [{"slug": "s", "boxes": [{"slug": "n", "name": "N"}]}],
        "clients": [{"client_id": "c", "organisation": "s"}],
    }

    assert plan(data).ok


def test_an_organisation_document_cannot_name_anything_outside_its_organisation() -> None:
    data = {
        "scope": {"organisation": "s"},
        "platform": {"settings": {"a": 1}},
        "shops": [{"slug": "t", "name": "T"}],
        "clients": [{"client_id": "c", "organisation": "t"}],
    }

    result = plan(data)

    assert refusals(result) == [
        ("platform", ErrorCode.CONFIGURATION_OUT_OF_SCOPE, None),
        ("shops[t]", ErrorCode.CONFIGURATION_OUT_OF_SCOPE, None),
        ("clients[c]", ErrorCode.CONFIGURATION_OUT_OF_SCOPE, None),
    ]
    assert result.changes == ()


def test_a_client_cannot_be_moved_into_the_scope_from_another_organisation() -> None:
    current = installation(
        shop("s"),
        shop("t"),
        clients=(entity("client", ("c",), {"organisation": "t", "enabled": True}),),
    )
    data = {"scope": {"organisation": "s"}, "clients": [{"client_id": "c", "organisation": "s"}]}

    assert refusals(plan(data, current)) == [
        ("clients[c]", ErrorCode.CONFIGURATION_OUT_OF_SCOPE, None)
    ]


def test_replace_in_an_organisation_scope_deletes_only_within_it() -> None:
    clients = (
        entity("client", ("mine",), {"organisation": "s", "enabled": False}),
        entity("client", ("theirs",), {"organisation": "t", "enabled": False}),
    )
    current = installation(shop("s"), shop("t"), clients=clients)

    result = plan({"mode": "replace", "scope": {"organisation": "s"}, "clients": []}, current)

    assert kinds(result) == [("client", ("mine",), ChangeAction.DELETE)]
    assert result.changes[0].organisation == "s"


def test_something_that_outside_facts_refer_to_is_not_deleted() -> None:
    usage = Usage(in_use={"box": frozenset({B})})

    result = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "action": "delete"}]}]}, usage=usage
    )

    assert refusals(result) == [("shops[s].boxes[b]", ErrorCode.CONFIGURATION_OBJECT_IN_USE, None)]


def test_deleting_a_parent_checks_everything_it_would_take_with_it() -> None:
    usage = Usage(in_use={"box": frozenset({B})})
    data = {"shops": [{"slug": "s", "action": "delete"}]}

    refused = plan(data, usage=usage)
    free = plan(data)

    assert refusals(refused) == [("shops[s]", ErrorCode.CONFIGURATION_OBJECT_IN_USE, None)]
    assert kinds(free) == [("shop", ("s",), ChangeAction.DELETE)]
    assert free.changes[0].permissions == {Permission.ORGANISATION_DELETE}


def test_replace_refuses_to_delete_what_is_in_use_too() -> None:
    usage = Usage(in_use={"box": frozenset({B})})
    current = installation(shop("s", box("b"), box("c")))

    result = plan(
        {"mode": "replace", "shops": [{"slug": "s", "boxes": [{"slug": "c"}]}]},
        current,
        usage=usage,
    )

    assert refusals(result) == [("shops[s].boxes[b]", ErrorCode.CONFIGURATION_OBJECT_IN_USE, None)]


def test_every_refusal_is_collected_not_only_the_first() -> None:
    data = {
        "shops": [
            {
                "slug": "s",
                "boxes": [
                    {"slug": "n"},
                    {"slug": "b", "action": "create", "name": "x"},
                    {"slug": "m", "action": "update"},
                ],
            }
        ]
    }

    assert [r.code for r in plan(data).refusals] == [
        ErrorCode.INVALID_CONFIGURATION,
        ErrorCode.CONFIGURATION_OBJECT_EXISTS,
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
    ]


def test_a_rule_of_the_kind_runs_on_the_fields_as_they_will_be_and_may_normalise_them() -> None:
    seen: list[tuple[dict[str, Value], dict[str, Value] | None]] = []

    def check(
        merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
    ) -> Mapping[str, Value]:
        seen.append((dict(merged), None if current is None else dict(current)))
        if merged["name"] == "bad":
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "name")
        return {**merged, "name": str(merged["name"]).title()}

    fmt = format_with_box(replace(BOX, check=check))

    created = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n", "name": "new box"}]}]}, fmt=fmt)
    refused = plan({"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "bad"}]}]}, fmt=fmt)

    assert created.changes[0].values["name"] == "New Box"
    assert created.changes[0].values.get("slug") is None
    assert seen[0][0]["slug"] == "n"
    assert seen[0][1] is None
    assert refusals(refused) == [("shops[s].boxes[b]", ErrorCode.INVALID_CONFIGURATION, "name")]
    assert seen[1][1] is not None
    assert seen[1][1]["name"] == "Box"


def test_a_rule_of_a_field_runs_only_on_what_the_document_names() -> None:
    calls: list[Value] = []

    def check(value: Value, context: RuleContext) -> Value:
        calls.append(value)
        if value == "no":
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "note")
        return str(value).upper()

    fields = tuple(replace(f, check=check) if f.name == "note" else f for f in BOX.fields)
    fmt = format_with_box(replace(BOX, fields=fields))
    current = installation(shop("s", box("b", note="old")))

    untouched = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Other"}]}]}, current, fmt=fmt
    )
    normalised = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "note": "fine"}]}]}, current, fmt=fmt
    )
    refused = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "b", "note": "no"}]}]}, current, fmt=fmt
    )

    assert untouched.changes[0].fields == ("name",)
    assert normalised.changes[0].values["note"] == "FINE"
    assert refusals(refused) == [("shops[s].boxes[b]", ErrorCode.INVALID_CONFIGURATION, "note")]
    assert calls == ["fine", "no"]


def test_a_rule_across_objects_sees_what_would_result() -> None:
    seen: list[Resolution] = []

    def rule(resolution: Resolution) -> list[Refusal]:
        seen.append(resolution)
        names = [
            b.fields["name"]
            for s in resolution.resulting.children["shops"]
            for b in s.children["boxes"]
        ]
        return (
            [Refusal("shops", ErrorCode.INVALID_CONFIGURATION, "name")]
            if names.count("Same") > 1
            else []
        )

    fmt = build_format(rule)
    data = {
        "shops": [
            {"slug": "s", "boxes": [{"slug": "n", "name": "Same"}, {"slug": "m", "name": "Same"}]}
        ]
    }

    ok = plan({"shops": [{"slug": "s", "boxes": [{"slug": "n", "name": "Other"}]}]}, fmt=fmt)
    refused = plan(data, fmt=fmt)

    assert ok.ok
    assert refusals(refused) == [("shops", ErrorCode.INVALID_CONFIGURATION, "name")]
    resolution = seen[-1]
    assert [c.address for c in resolution.changes] == [("s", "n"), ("s", "m")]
    assert resolution.current.children["shops"][0].children["boxes"][0].key == ("b",)
    assert isinstance(resolution.context, RuleContext)


def test_writes_come_first_in_tree_order_and_deletions_last() -> None:
    current = installation(shop("s", box("a"), box("b")), shop("t"))
    data = {
        "mode": "replace",
        "shops": [{"slug": "s", "name": "S2", "boxes": [{"slug": "c", "name": "C"}]}],
    }

    result = plan(data, current)

    assert kinds(result) == [
        ("shop", ("s",), ChangeAction.UPDATE),
        ("box", ("s", "c"), ChangeAction.CREATE),
        ("box", ("s", "a"), ChangeAction.DELETE),
        ("box", ("s", "b"), ChangeAction.DELETE),
        ("shop", ("t",), ChangeAction.DELETE),
    ]


def test_the_plan_hash_follows_the_document_and_the_settings_version() -> None:
    data = {"shops": [{"slug": "s", "name": "Other"}]}

    one = plan(data, version=1)

    assert one.digest == plan(data, version=1).digest
    assert one.digest != plan(data, version=2).digest
    assert one.digest != plan({"shops": [{"slug": "s", "name": "Else"}]}, version=1).digest
    assert len(one.digest) == 64


def test_a_plan_describes_what_it_does_by_names_and_never_by_values() -> None:
    data = {
        "shops": [
            {
                "slug": "s",
                "boxes": [
                    {"slug": "n", "name": "Hidden value", "size": 3},
                    {"slug": "b", "action": "delete"},
                ],
            }
        ]
    }

    lines = plan(data).lines()

    assert lines == ["create box s/n: name, size", "delete box s/b"]
    assert all("Hidden value" not in line for line in lines)


def test_a_change_never_prints_its_values() -> None:
    (change,) = plan(
        {"shops": [{"slug": "s", "boxes": [{"slug": "n", "name": "Hidden value"}]}]}
    ).changes

    assert "Hidden value" not in repr(change)
    assert change.values["name"] == "Hidden value"


@pytest.mark.parametrize("version", [0, 7])
def test_the_resolution_holds_the_settings_context_the_rules_need(version: int) -> None:
    seen: list[Resolution] = []

    def rule(resolution: Resolution) -> list[Refusal]:
        seen.append(resolution)
        return []

    plan(
        {},
        fmt=build_format(rule),
        context=RuleContext(settings={"limit": version}),
        version=version,
    )

    assert seen[0].context.settings["limit"] == version


def test_walking_a_tree_yields_every_object_with_its_address() -> None:
    tags = (entity("tag", ("a",)),)
    root = installation(shop("s", box("b", _children={"tags": tags})), shop("t"))

    assert [(address, found.kind) for address, found in walk(root)] == [
        (("s",), "shop"),
        (("t",), "shop"),
        ((), "platform"),
        (("s", "b"), "box"),
        (("s", "b", "a"), "tag"),
    ]
    assert [address for address, _ in walk(root, "tag")] == [("s", "b", "a")]


def test_naming_an_unchanged_secret_still_needs_its_permission() -> None:
    current = installation(shop("s", box("b", password=Secret("same"))))

    allowed = plan_with_values(current, password=Secret("same"))
    probe = plan_with_values(current, password=Secret("same"), _may=Recorder(Permission.GROUP_EDIT))

    assert allowed.changes == ()
    assert [r.code for r in probe.refusals] == [ErrorCode.NOT_PERMITTED]


def test_replace_in_an_organisation_scope_deletes_below_the_top_level_too() -> None:
    current = installation(shop("s", box("a"), box("b")))
    data = {
        "mode": "replace",
        "scope": {"organisation": "s"},
        "shops": [{"slug": "s", "boxes": [{"slug": "a"}]}],
    }

    assert kinds(plan(data, current)) == [("box", ("s", "b"), ChangeAction.DELETE)]


def test_the_plan_hash_follows_what_the_plan_does() -> None:
    data = {"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Other"}]}]}
    one = plan(data, installation(shop("s", box("b"))))
    elsewhere = plan(data, installation(shop("s", box("b", name="Other")), shop("t")))

    assert one.digest != elsewhere.digest
