"""Flows, legacy pools and revisions in a configuration document (ADR 0121 step 2b-ii)."""

from dataclasses import replace
from typing import Any

from app.domain.authorization import Permission
from app.domain.configuration_changes import ChangeAction, Usage
from app.domain.errors import ErrorCode
from tests.support.configuration import Recorder
from tests.support.flow_documents import (
    acme,
    advies,
    document_json,
    draft,
    empty_flows,
    entity,
    flow,
    flows_of,
    full,
    group,
    installation,
    legacy_pool,
    legacy_rev,
    organisation,
    plan,
    published,
    refusals,
    revision,
    skill,
)

FLOW = "organisations[acme].groups[balie].flows[advies]"
APPOINTMENT = "organisations[acme].groups[balie].flows[appointment]"


def test_what_an_export_writes_plans_to_nothing_against_the_same_installation() -> None:
    result = plan(document_json(), full())

    assert result.ok
    assert result.changes == ()


def test_the_document_carries_every_revision_and_names_the_current_and_the_draft() -> None:
    flow_json = advies(document_json())

    assert [r["revision"] for r in flow_json["revisions"]] == [1, 2, 3]
    assert (flow_json["current_revision"], flow_json["draft_revision"]) == (2, 3)
    assert flow_json["revisions"][2]["published_at"] is None
    assert flow_json["revisions"][1]["published_at"] == published(2)


def test_no_database_id_and_no_staff_identity_is_in_the_document() -> None:
    text = repr(document_json())

    assert "published_by" not in text
    for name in ("capacity_pool_id", "skill_id", "pool_id", "flow_id", "staff"):
        assert f"'{name}'" not in text


def test_into_an_installation_without_them_everything_is_created_with_no_refusal() -> None:
    result = plan(document_json(), empty_flows())

    assert result.ok
    created = [(c.kind, "/".join(c.address)) for c in result.changes]
    assert ("skill", "acme/passport") in created
    assert ("skill", "acme/old") in created
    assert ("capacity_pool", "acme/loket") in created
    assert ("legacy_capacity_pool", "acme/balie") in created
    assert ("service_flow", "acme/balie/advies") in created
    assert [
        c for c in created if c[0] == "flow_revision" and c[1].startswith("acme/balie/advies")
    ] == [("flow_revision", f"acme/balie/advies/{n}") for n in (1, 2, 3)]
    assert all(c.action is ChangeAction.CREATE for c in result.changes if c.kind != "group")


def test_the_skills_of_a_subject_reach_the_change_the_adapter_writes() -> None:
    result = plan(document_json(), empty_flows())

    second = next(
        c for c in result.changes if c.kind == "flow_revision" and c.address[-2:] == ("advies", "2")
    )
    assert second.values["subjects"] == {"paspoort": ["passport"]}
    assert second.values["required_skills"] == ["passport"]


def test_a_subject_skill_that_is_no_code_is_refused() -> None:
    data = document_json()
    draft(data)["subjects"] = {"paspoort": ["Not A Code"]}

    assert (
        f"{FLOW}.revisions[3]",
        ErrorCode.INVALID_CONFIGURATION,
        "subject_skill_ids",
    ) in refusals(plan(data, empty_flows()))


def test_one_pool_serves_the_flows_of_two_services() -> None:
    data = document_json()
    pools = {
        r["capacity_pool"]
        for g in acme(data)["groups"]
        for f in g.get("flows", [])
        for r in f["revisions"]
        if r["capacity_pool"]
    }

    assert pools == {"loket"}
    assert plan(data, empty_flows()).ok


# --- permissions ------------------------------------------------------------------------


def test_the_three_sections_are_checked_against_their_own_permissions() -> None:
    recorder = Recorder()

    plan(document_json(), empty_flows(), may_change=recorder)

    asked = {permission for permission, _ in recorder.asked}
    assert {Permission.SKILLS_MANAGE, Permission.CAPACITY_MANAGE, Permission.GROUP_EDIT} <= asked


def test_a_service_admin_without_the_catalogue_rights_cannot_import_skills() -> None:
    result = plan(document_json(), empty_flows(), may_change=Recorder(Permission.SKILLS_MANAGE))

    assert {r.code for r in result.refusals} == {ErrorCode.NOT_PERMITTED}
    assert all("skills[" in r.path for r in result.refusals)


# --- absent is not empty, replace archives ----------------------------------------------


def replacing(*, strip: bool = False, empty: bool = False) -> dict[str, Any]:
    """Return the full export as a ``replace`` document, stripped of the new sections or empty."""
    data = document_json()
    data["mode"] = "replace"
    for org in data["organisations"]:
        for section in ("skills", "capacity_pools"):
            if strip:
                org.pop(section, None)
            elif empty and org["slug"] == "acme":
                org[section] = []
        for group_json in org["groups"]:
            if strip:
                group_json.pop("flows", None)
                group_json.pop("legacy_pool", None)
            elif empty and org["slug"] == "acme":
                group_json["flows"] = []
    return data


def test_a_document_without_the_sections_leaves_skills_pools_and_flows_alone() -> None:
    older = replacing(strip=True)

    result = plan(older, full())

    assert result.ok
    assert result.changes == ()


def test_an_empty_section_in_a_replace_document_means_none() -> None:
    result = plan(replacing(empty=True), full())

    actions = {(c.kind, "/".join(c.address)): c.action for c in result.changes}
    assert actions[("skill", "acme/passport")] is ChangeAction.DELETE
    assert actions[("capacity_pool", "acme/loket")] is ChangeAction.DELETE
    assert actions[("service_flow", "acme/balie/advies")] is ChangeAction.DELETE
    assert actions[("service_flow", "acme/kantoor/telefoon")] is ChangeAction.DELETE
    assert not any(kind == "flow_revision" for kind, _ in actions)


def test_what_a_replace_document_leaves_out_is_archived_when_something_refers_to_it() -> None:
    usage = Usage(
        kept={
            "skill": frozenset({("acme", "passport"), ("acme", "old")}),
            "capacity_pool": frozenset({("acme", "loket")}),
            "service_flow": frozenset({("acme", "balie", "appointment")}),
        }
    )
    data = replacing(empty=True)
    data["organisations"][0]["groups"][1]["flows"] = flows_of(
        document_json(), 0, 1
    )  # telefoon stays

    result = plan(data, full(), usage=usage)

    actions = {(c.kind, "/".join(c.address)): c.action for c in result.changes}
    assert actions[("skill", "acme/passport")] is ChangeAction.ARCHIVE
    assert ("skill", "acme/old") not in actions  # archived already, nothing to do
    assert actions[("service_flow", "acme/balie/appointment")] is ChangeAction.ARCHIVE
    assert actions[("service_flow", "acme/balie/advies")] is ChangeAction.DELETE
    assert (
        "organisations[acme].capacity_pools[loket]",
        ErrorCode.CONFIGURATION_OBJECT_IN_USE,
        "capacity_pool",
    ) in refusals(result)  # the flow telefoon is not archived and still draws on the pool


def test_an_archive_change_says_what_it_marks_and_never_deletes() -> None:
    usage = Usage(kept={"skill": frozenset({("acme", "passport")})})
    data = {
        "format": "openplanner.config/v1",
        "mode": "replace",
        "organisations": [
            {"slug": "acme", "skills": [{"code": "id", "labels": {"nl": "I", "en": "I"}}]}
        ],
    }
    current = installation(organisation("acme", group("balie"), skills=(skill("passport"),)))

    result = plan(data, current, usage=usage)

    archive = next(c for c in result.changes if c.action is ChangeAction.ARCHIVE)
    assert (archive.kind, archive.fields, archive.values["archived"]) == (
        "skill",
        ("archived",),
        True,
    )
    assert any(line.startswith("archive skill acme/passport") for line in result.lines())
    assert not any(c.action is ChangeAction.DELETE for c in result.changes if c.kind == "skill")


def test_an_organisation_scoped_document_may_carry_the_sections() -> None:
    data = document_json()
    data["scope"] = {"organisation": "acme"}
    del data["organisations"][1]

    assert plan(data, empty_flows()).ok


def test_a_service_without_a_legacy_pool_gets_it_from_the_document() -> None:
    current = installation(organisation("acme", group("balie")))
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {
                        "slug": "balie",
                        "legacy_pool": {"name": "Balie", "guaranteed_skills": []},
                    }
                ],
            }
        ],
    }

    result = plan(data, current)

    assert result.ok
    (change,) = result.changes
    assert change.kind == "legacy_capacity_pool"
    assert change.values["name"] == "Balie"


def test_a_legacy_pool_cannot_be_deleted_by_a_document() -> None:
    current = installation(organisation("acme", group("balie", legacy="Balie")))
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {"slug": "acme", "groups": [{"slug": "balie", "legacy_pool": {"action": "delete"}}]}
        ],
    }

    assert [r.code for r in plan(data, current).refusals] == [ErrorCode.INVALID_CONFIGURATION]


def test_a_service_whose_flow_is_booked_is_not_deleted_with_its_flow() -> None:
    usage = Usage(kept={"service_flow": frozenset({("acme", "balie", "appointment")})})
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "groups": [
                    {"slug": "balie", "flows": [{"action": "delete", "code": "appointment"}]}
                ],
            }
        ],
    }

    result = plan(data, full(), usage=usage)

    assert [(c.kind, c.action) for c in result.changes] == [("service_flow", ChangeAction.ARCHIVE)]


def test_helpers_build_what_they_say() -> None:
    assert replace(legacy_pool("X"), key=()).kind == "legacy_capacity_pool"
    assert entity("x").children == {}
    assert revision(1).key == ("1",)
    assert legacy_rev("g").fields["legacy"] is True
    assert flow("f", "draft", None, 1, []).fields["draft_revision"] == 1
