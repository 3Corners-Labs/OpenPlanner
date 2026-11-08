"""Prepared closure scenarios and their images in a document (ADR 0037, 0099)."""

import base64
import hashlib
import struct
import zlib
from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction
from app.domain.configuration_document import to_json
from app.domain.configuration_v1_scenarios import (
    GROUP_SCENARIO,
    GROUP_SCENARIOS,
    IMAGE,
    INTEGRITY,
    ORGANISATION_SCENARIO,
    ORGANISATION_SCENARIOS,
)
from app.domain.configuration_values import Binary
from app.domain.errors import ErrorCode
from tests.support.configuration import Recorder, entity, parse
from tests.support.configuration_columns import uncovered_columns
from tests.support.configuration_parts import installation, organisation, plan_opened, stub_format

FMT = stub_format((ORGANISATION_SCENARIOS,), (GROUP_SCENARIOS,), *INTEGRITY)
ORG_PATH = "organisations[acme].closure_scenarios[Storing]"
GROUP_PATH = "organisations[acme].groups[helpdesk].closure_scenarios[Storing]"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


# A 1x1 PNG with a text chunk, which the stripper removes.
PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + _chunk(b"tEXt", b"author\x00Jan")
    + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    + _chunk(b"IEND", b"")
)


def binary(data: bytes, media_type: str = "image/png") -> dict[str, str]:
    return {
        "media_type": media_type,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode(),
    }


def scenario(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": "Storing",
        "notices": {"nl": "Wegens storing zijn we gesloten.", "en": "Closed due to an outage."},
    }
    values.update(overrides)
    return values


def org_doc(*scenarios: dict[str, Any]) -> dict[str, Any]:
    return {"organisations": [{"slug": "acme", "closure_scenarios": list(scenarios)}]}


def group_doc(*scenarios: dict[str, Any]) -> dict[str, Any]:
    group = {"slug": "helpdesk", "closure_scenarios": list(scenarios)}
    return {"organisations": [{"slug": "acme", "groups": [group]}]}


def empty() -> Any:
    group = entity("group", ("helpdesk",))
    return installation(organisation("acme", groups=(group,)))


def refusals(result: Any) -> list[tuple[str, ErrorCode, str | None]]:
    return [(r.path, r.code, r.field) for r in result.refusals]


def test_an_organisation_scenario_is_created_by_the_closure_screen_of_the_organisation() -> None:
    recorder = Recorder()

    result = plan_opened(org_doc(scenario()), empty(), FMT, may_change=recorder)

    (change,) = result.changes
    assert result.ok
    assert (change.kind, change.address, change.action) == (
        "organisation_scenario",
        ("acme", "Storing"),
        ChangeAction.CREATE,
    )
    assert change.values["headings"] == {}
    assert change.values["contacts"] is None
    assert change.permissions == {Permission.ORGANISATION_CLOSURE}
    assert recorder.asked == [(Permission.ORGANISATION_CLOSURE, Target.for_organisation("acme"))]


def test_a_group_scenario_is_created_by_the_closure_screen_of_the_group() -> None:
    recorder = Recorder()

    result = plan_opened(group_doc(scenario()), empty(), FMT, may_change=recorder)

    (change,) = result.changes
    assert (change.kind, change.address, change.group) == (
        "group_scenario",
        ("acme", "helpdesk", "Storing"),
        "helpdesk",
    )
    assert recorder.asked == [(Permission.CLOSURE_MANAGE, Target.for_group("helpdesk", "acme"))]


def test_the_same_name_may_exist_for_the_organisation_and_for_a_group() -> None:
    data = {
        "organisations": [
            {
                "slug": "acme",
                "closure_scenarios": [scenario()],
                "groups": [{"slug": "helpdesk", "closure_scenarios": [scenario()]}],
            }
        ]
    }

    assert [c.kind for c in plan_opened(data, empty(), FMT).changes] == [
        "organisation_scenario",
        "group_scenario",
    ]


def test_text_is_normalised_as_the_closure_screen_does() -> None:
    data = org_doc(
        scenario(
            notices={"nl": "  Regel een\r\nRegel twee ", "en": "Closed", "de": "ignored"},
            headings={"nl": " Kop ", "en": "  "},
        )
    )

    (change,) = plan_opened(data, empty(), FMT).changes

    assert change.values["notices"] == {"nl": "Regel een\nRegel twee", "en": "Closed"}
    assert change.values["headings"] == {"nl": "Kop"}


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"notices": {"nl": "Alleen Nederlands"}}, "notice_en"),
        ({"notices": {"nl": "x", "en": "y" * 1001}}, "notice_en"),
        ({"notices": {"nl": 5, "en": "y"}}, "notice_nl"),
        ({"headings": {"nl": "x" * 121}}, "heading_nl"),
        ({"headings": {"fr": "Fermé"}}, "heading_fr"),
        ({"headings": {"nl": 1}}, "heading_nl"),
        ({"name": "Eén\nregel"}, "scenario_name"),
        ({"contacts": {"options": [{"kind": "phone", "value": "x"}]}}, "contact_1_phone"),
    ],
)
def test_a_scenario_the_screen_would_refuse_is_refused_by_field(
    overrides: dict[str, Any], field: str
) -> None:
    result = plan_opened(org_doc(scenario(**overrides)), empty(), FMT)

    assert [(r.code, r.field) for r in result.refusals] == [
        (ErrorCode.INVALID_CONFIGURATION, field)
    ]
    assert result.refusals[0].path.startswith("organisations[acme].closure_scenarios[")


def test_a_contact_block_is_normalised_and_an_empty_one_is_no_block() -> None:
    phone = {"kind": "phone", "value": " 030 123 4567 "}

    block = plan_opened(org_doc(scenario(contacts={"options": [phone]})), empty(), FMT)
    none = plan_opened(org_doc(scenario(contacts={"options": []})), empty(), FMT)

    assert block.changes[0].values["contacts"]["options"][0]["value"] == "030 123 4567"  # type: ignore[index]
    assert none.changes[0].values["contacts"] is None


def test_an_update_names_only_what_changed_and_a_delete_removes_the_scenario() -> None:
    stored = entity(
        "organisation_scenario",
        ("Storing",),
        {"notices": scenario()["notices"], "headings": {}, "contacts": None},
    )
    tree = installation(organisation("acme", closure_scenarios=(stored,)))

    same = plan_opened(org_doc(scenario()), tree, FMT)
    edited = plan_opened(org_doc(scenario(headings={"nl": "Gesloten"})), tree, FMT)
    removed = plan_opened(org_doc({"name": "Storing", "action": "delete"}), tree, FMT)

    assert same.changes == ()
    assert [(c.action, c.fields) for c in edited.changes] == [(ChangeAction.UPDATE, ("headings",))]
    assert [c.action for c in removed.changes] == [ChangeAction.DELETE]


def test_an_image_is_stored_without_its_metadata_and_a_second_plan_sees_no_change() -> None:
    image = {"data": binary(PNG), "alts": {"nl": "Een foto", "en": "A photo"}}
    data = org_doc(scenario(image=image))

    first = plan_opened(data, empty(), FMT)
    stored = first.changes[1].values

    assert [c.kind for c in first.changes] == ["organisation_scenario", "scenario_image"]
    assert isinstance(stored["data"], Binary)
    assert b"tEXt" not in stored["data"].data
    assert stored["decorative"] is False
    tree = installation(
        organisation(
            "acme",
            closure_scenarios=(
                entity(
                    "organisation_scenario",
                    ("Storing",),
                    first.changes[0].values,
                    {"image": (entity("scenario_image", (), stored),)},
                ),
            ),
        )
    )
    assert plan_opened(data, tree, FMT).changes == ()


def test_an_image_needs_both_text_alternatives_unless_it_is_decorative() -> None:
    path = ORG_PATH + ".image"
    bare = scenario(image={"data": binary(PNG)})
    decorative = scenario(image={"data": binary(PNG), "decorative": True, "alts": {"nl": "weg"}})
    one = scenario(image={"data": binary(PNG), "alts": {"nl": "Een foto"}})

    refused = plan_opened(org_doc(bare), empty(), FMT)
    ok = plan_opened(org_doc(decorative), empty(), FMT)
    partly = plan_opened(org_doc(one), empty(), FMT)

    assert refusals(refused) == [(path, ErrorCode.INVALID_CONFIGURATION, "alt_nl")]
    assert refusals(partly) == [(path, ErrorCode.INVALID_CONFIGURATION, "alt_en")]
    assert ok.changes[1].values["alts"] == {}


def test_a_file_that_is_no_supported_image_is_refused() -> None:
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    image = {"data": binary(svg, "image/svg+xml"), "decorative": True}

    result = plan_opened(org_doc(scenario(image=image)), empty(), FMT)

    assert (ORG_PATH + ".image", ErrorCode.INVALID_CONFIGURATION, "image") in refusals(result)


def test_an_image_alt_must_be_a_json_object_of_texts() -> None:
    image = {"data": binary(PNG), "alts": {"nl": 1, "en": "x"}}

    result = plan_opened(org_doc(scenario(image=image)), empty(), FMT)

    assert refusals(result) == [(ORG_PATH + ".image", ErrorCode.INVALID_CONFIGURATION, "alt_nl")]


def test_an_image_can_be_removed_and_a_group_image_follows_the_group_screen() -> None:
    recorder = Recorder()
    image = entity("scenario_image", (), {"data": Binary("image/png", PNG), "alts": {}})
    stored = entity("group_scenario", ("Storing",), scenario(), {"image": (image,)})
    group = entity("group", ("helpdesk",), {}, {"closure_scenarios": (stored,)})
    tree = installation(organisation("acme", groups=(group,)))
    data = group_doc({"name": "Storing", "image": {"action": "delete"}})

    result = plan_opened(data, tree, FMT, may_change=recorder)

    assert [(c.kind, c.action) for c in result.changes] == [("scenario_image", ChangeAction.DELETE)]
    assert recorder.asked == [(Permission.CLOSURE_MANAGE, Target.for_group("helpdesk", "acme"))]


def test_a_scenario_with_an_image_reads_and_writes_the_same_document() -> None:
    data = group_doc(
        scenario(
            headings={"nl": "Kop"},
            contacts={"headings": {}, "options": []},
            image={"data": binary(PNG), "alts": {"nl": "a", "en": "b"}, "decorative": False},
        )
    )

    assert to_json(parse(data, FMT)) == {"format": "test/v1", **data}


def test_the_kinds_cover_every_column_of_their_tables() -> None:
    assert uncovered_columns([ORGANISATION_SCENARIO, GROUP_SCENARIO, IMAGE]) == {}
