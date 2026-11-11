"""Skills, pools and flows in a configuration document (ADR 0121 step 2b-ii, ADR 0099)."""

import copy
from dataclasses import replace
from typing import Any

import pytest

from app.domain.authorization import Permission
from app.domain.configuration_changes import ChangeAction, Usage
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import Recorder
from tests.support.flow_documents import (
    document_json,
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
    pool,
    revision,
    skill,
)

FLOW = "organisations[acme].groups[balie].flows[advies]"
APPOINTMENT = "organisations[acme].groups[balie].flows[appointment]"


def refusals(result: Any) -> list[tuple[str, ErrorCode, str | None]]:
    return [(r.path, r.code, r.field) for r in result.refusals]


def advies(data: dict[str, Any]) -> dict[str, Any]:
    return next(f for f in flows_of(data) if f["code"] == "advies")


def acme(data: dict[str, Any]) -> dict[str, Any]:
    return data["organisations"][0]  # type: ignore[no-any-return]


def test_what_an_export_writes_plans_to_nothing_against_the_same_installation() -> None:
    result = plan(document_json(), full())

    assert result.ok
    assert result.changes == ()


def test_the_document_carries_every_revision_and_names_the_current_and_the_draft() -> None:
    flow_json = advies(document_json())

    assert [r["revision"] for r in flow_json["revisions"]] == [1, 2, 3]
    assert (flow_json["current_revision"], flow_json["draft_revision"]) == (2, 3)
    assert flow_json["revisions"][2]["published_at"] is None
    assert flow_json["revisions"][1]["published_at"] == "2026-11-01T10:00:00+00:00"


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


# --- skills and counted pools -----------------------------------------------------------


def test_a_skill_needs_both_labels_and_a_code_of_the_subject_grammar() -> None:
    data = document_json()
    acme(data)["skills"][0]["labels"] = {"nl": "Alleen Nederlands"}
    acme(data)["skills"][1]["code"] = "Not A Code"

    result = plan(data, empty_flows())

    found = refusals(result)
    assert (
        "organisations[acme].skills[passport]",
        ErrorCode.INVALID_CONFIGURATION,
        "skill_label",
    ) in found
    assert (
        "organisations[acme].skills[Not A Code]",
        ErrorCode.INVALID_CONFIGURATION,
        "skill_code",
    ) in found


def test_a_pool_cannot_guarantee_a_skill_the_organisation_does_not_have() -> None:
    data = document_json()
    acme(data)["capacity_pools"][0]["guaranteed_skills"] = ["id-card"]  # beta's skill

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (
            "organisations[acme].capacity_pools[loket]",
            ErrorCode.CONFIGURATION_OBJECT_MISSING,
            "guaranteed_skill_ids",
        )
    ]


def test_a_pool_may_keep_an_archived_skill_but_never_newly_choose_one() -> None:
    kept = installation(
        organisation(
            "acme",
            group("balie"),
            skills=(skill("old", archived=True),),
            capacity_pools=(pool("loket", "old"),),
        )
    )
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {"slug": "acme", "capacity_pools": [{"name": "loket", "guaranteed_skills": ["old"]}]}
        ],
    }
    assert plan(data, kept).ok

    fresh = installation(
        organisation(
            "acme",
            group("balie"),
            skills=(skill("old", archived=True),),
            capacity_pools=(pool("loket"),),
        )
    )
    assert refusals(plan(data, fresh)) == [
        (
            "organisations[acme].capacity_pools[loket]",
            ErrorCode.INVALID_CONFIGURATION,
            "guaranteed_skill_ids",
        )
    ]


def test_two_pools_with_one_name_in_an_organisation_are_refused_when_read() -> None:
    data = document_json()
    acme(data)["capacity_pools"].append({"name": "loket"})

    with pytest.raises(DomainError) as refused:
        plan(data, empty_flows())

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == "organisations[acme].capacity_pools[loket]"


def test_a_pool_is_not_archived_while_a_flow_that_is_not_archived_draws_on_it() -> None:
    data = document_json()
    acme(data)["capacity_pools"][0]["archived"] = True

    result = plan(data, full())

    assert refusals(result) == [
        (
            "organisations[acme].capacity_pools[loket]",
            ErrorCode.CONFIGURATION_OBJECT_IN_USE,
            "capacity_pool",
        )
    ]


# --- references -------------------------------------------------------------------------


def draft(data: dict[str, Any]) -> dict[str, Any]:
    return advies(data)["revisions"][2]  # type: ignore[no-any-return]


def test_a_draft_cannot_name_a_pool_or_skill_that_does_not_exist() -> None:
    data = document_json()
    draft(data)["capacity_pool"] = "elders"
    draft(data)["required_skills"] = ["passport", "unknown"]

    result = plan(data, empty_flows())

    assert (
        f"{FLOW}.revisions[3]",
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "capacity_pool_id",
    ) in refusals(result)


def test_a_flow_cannot_use_the_legacy_pool_of_a_service_of_another_organisation() -> None:
    data = document_json()
    draft(data)["capacity_pool"] = None
    draft(data)["legacy_pool_of"] = "vestiging"

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "capacity_pool_id")
    ]


def test_a_flow_cannot_use_a_skill_that_only_another_organisation_has() -> None:
    data = document_json()
    draft(data)["required_skills"] = ["id-card"]  # beta's skill

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.CONFIGURATION_OBJECT_MISSING, "required_skill_ids")
    ]


def test_a_draft_cannot_offer_a_subject_the_service_does_not_have() -> None:
    data = document_json()
    draft(data)["subjects"] = {"rijbewijs": ["passport"]}

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "subject_skill_ids")
    ]


def test_a_published_revision_may_keep_a_subject_that_is_gone_so_an_export_imports_again() -> None:
    data = document_json()
    advies(data)["revisions"][1]["subjects"] = {"rijbewijs": ["passport"]}

    assert plan(data, empty_flows()).ok


def test_a_published_revision_may_name_a_skill_archived_afterwards() -> None:
    data = document_json()
    advies(data)["revisions"][0]["required_skills"] = ["old"]

    assert plan(data, empty_flows()).ok


def test_a_draft_cannot_newly_choose_an_archived_skill() -> None:
    data = document_json()
    draft(data)["required_skills"] = ["old"]

    result = plan(data, full())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "required_skill_ids")
    ]


def test_a_document_cannot_set_a_location() -> None:
    data = document_json()
    draft(data)["location_id"] = 3

    with pytest.raises(DomainError) as refused:
        plan(data, empty_flows())

    assert refused.value.code is ErrorCode.CONFIGURATION_UNKNOWN_FIELD


def test_an_interaction_kind_and_a_handling_action_must_fit_the_closed_table() -> None:
    data = document_json()
    draft(data)["handling_action"] = "open_video"  # a telephone flow does not open video

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "handling_action")
    ]


def test_a_revision_names_one_pool_not_two() -> None:
    data = document_json()
    draft(data)["legacy_pool_of"] = "balie"

    assert refusals(plan(data, empty_flows())) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "capacity_pool_id")
    ]


# --- flows ------------------------------------------------------------------------------


def test_two_active_flows_cannot_both_be_the_default_of_one_entry_path() -> None:
    data = document_json()
    second = copy.deepcopy(flows_of(data)[0])
    second["code"] = "second"
    flows_of(data).append(second)

    result = plan(data, empty_flows())

    assert ErrorCode.CONFIGURATION_OBJECT_EXISTS in {r.code for r in result.refusals}
    assert {
        r.field for r in result.refusals if r.code is ErrorCode.CONFIGURATION_OBJECT_EXISTS
    } == {"default_for"}


def test_a_flow_names_a_current_revision_the_document_holds() -> None:
    data = document_json()
    advies(data)["current_revision"] = 7

    result = plan(data, empty_flows())

    assert (FLOW, ErrorCode.CONFIGURATION_OBJECT_MISSING, "current_revision") in refusals(result)


def test_a_flow_needs_a_current_or_a_draft_and_an_active_flow_a_current() -> None:
    data = document_json()
    advies(data)["current_revision"] = None
    advies(data)["draft_revision"] = None

    assert (FLOW, ErrorCode.INVALID_CONFIGURATION, "revision") in refusals(
        plan(data, empty_flows())
    )

    data = document_json()
    advies(data)["state"] = "active"
    advies(data)["current_revision"] = None

    assert (FLOW, ErrorCode.INVALID_CONFIGURATION, "state") in refusals(plan(data, empty_flows()))


def test_the_current_revision_is_published_and_only_the_draft_is_not() -> None:
    data = document_json()
    advies(data)["draft_revision"] = None  # revision 3 is unpublished but no longer the draft

    result = plan(data, empty_flows())

    assert (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "published_at") in refusals(
        result
    )


def test_a_time_without_an_offset_is_not_a_publication_time() -> None:
    data = document_json()
    advies(data)["revisions"][0]["published_at"] = "2026-11-01T10:00:00"

    result = plan(data, empty_flows())

    assert (f"{FLOW}.revisions[1]", ErrorCode.INVALID_CONFIGURATION, "published_at") in refusals(
        result
    )


def test_a_document_cannot_bring_a_flow_live_that_the_readiness_checklist_refuses() -> None:
    data = document_json()
    advies(data)["state"] = "active"  # a telephone flow on the own queue is built, retention is not

    result = plan(data, empty_flows())

    assert (FLOW, ErrorCode.INVALID_CONFIGURATION, "readiness_retention") in refusals(result)


def test_a_flow_may_only_change_state_as_the_lifecycle_allows() -> None:
    data = document_json()
    flows_of(data)[0]["state"] = "draft"  # active to draft does not exist

    result = plan(data, full())

    assert (APPOINTMENT, ErrorCode.INVALID_CONFIGURATION, "state") in refusals(result)


def test_an_active_flow_may_be_archived_by_a_document() -> None:
    data = document_json()
    flows_of(data)[0]["state"] = "archived"

    result = plan(data, full())

    assert result.ok
    assert [(c.kind, c.action) for c in result.changes] == [("service_flow", ChangeAction.UPDATE)]


# --- legacy flows -----------------------------------------------------------------------


def test_a_legacy_revision_must_be_exactly_the_wrapper_the_backfill_writes() -> None:
    data = document_json()
    flows_of(data)[0]["revisions"][0]["completion"] = "operator"  # not the wrapper

    result = plan(data, empty_flows())

    assert (f"{APPOINTMENT}.revisions[1]", ErrorCode.INVALID_CONFIGURATION, "legacy") in refusals(
        result
    )


def test_the_legacy_flag_is_no_way_around_the_checklist() -> None:
    data = document_json()
    other = flows_of(data, 0, 1)[0]["revisions"][0]
    other["legacy"] = True  # a telephone revision is not the wrapper

    result = plan(data, empty_flows())

    assert any(r.field == "legacy" for r in result.refusals)


def test_a_legacy_revision_draws_on_the_legacy_pool_of_its_own_service() -> None:
    data = document_json()
    flows_of(data)[0]["revisions"][0]["legacy_pool_of"] = "kantoor"

    result = plan(data, empty_flows())

    assert any(r.field == "legacy" for r in result.refusals)


# --- revisions are history --------------------------------------------------------------


def test_a_published_revision_is_never_changed_by_a_document() -> None:
    data = document_json()
    advies(data)["revisions"][1]["labels"] = {"nl": "Anders", "en": "Other"}

    result = plan(data, full())

    assert refusals(result) == [
        (f"{FLOW}.revisions[2]", ErrorCode.CONFIGURATION_REVISION_PUBLISHED, "labels")
    ]


def test_a_published_revision_cannot_be_deleted_by_a_document() -> None:
    data = document_json()
    advies(data)["revisions"][0] = {"revision": 1, "action": "delete"}

    result = plan(data, full())

    assert (
        f"{FLOW}.revisions[1]",
        ErrorCode.CONFIGURATION_REVISION_PUBLISHED,
        "published_at",
    ) in refusals(result)


def test_a_draft_may_be_replaced() -> None:
    data = document_json()
    draft(data)["labels"] = {"nl": "Anders", "en": "Other"}

    result = plan(data, full())

    assert result.ok
    (change,) = result.changes
    assert (change.kind, change.action, change.key, change.fields) == (
        "flow_revision",
        ChangeAction.UPDATE,
        ("3",),
        ("labels",),
    )


def test_a_new_revision_is_added_and_the_old_ones_stay_numbered_as_they_were() -> None:
    data = document_json()
    flow_json = advies(data)
    flow_json["revisions"].append({**copy.deepcopy(flow_json["revisions"][1]), "revision": 4})
    flow_json["current_revision"], flow_json["draft_revision"] = 4, None
    flow_json["revisions"][2]["published_at"] = "2026-11-02T10:00:00+00:00"  # the draft, published
    flow_json["revisions"][2]["required_skills"] = []

    result = plan(data, full())

    created = [(c.kind, c.key) for c in result.changes if c.action is ChangeAction.CREATE]
    assert created == [("flow_revision", ("4",))]


def test_a_replace_document_never_trims_the_revisions_it_leaves_out() -> None:
    data = document_json()
    advies(data)["revisions"] = [advies(data)["revisions"][1], advies(data)["revisions"][2]]
    data["mode"] = "replace"

    result = plan(data, full())

    assert result.ok
    assert result.changes == ()


def test_a_revision_number_is_a_number_in_the_document_and_text_in_the_tree() -> None:
    data = document_json()

    assert [r["revision"] for r in advies(data)["revisions"]] == [1, 2, 3]
    data["organisations"][0]["groups"][0]["flows"][1]["revisions"][0]["revision"] = "1"
    with pytest.raises(DomainError):
        plan(data, full())


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
        in_use={
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
    usage = Usage(in_use={"skill": frozenset({("acme", "passport")})})
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
    usage = Usage(in_use={"service_flow": frozenset({("acme", "balie", "appointment")})})
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
