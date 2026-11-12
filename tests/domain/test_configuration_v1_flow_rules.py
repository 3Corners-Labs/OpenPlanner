"""The rules across skills, pools and flows of a document (ADR 0121 step 2b-ii, ADR 0099)."""

import copy
from typing import Any

import pytest

from app.domain import configuration_v1_flow_rules as rules
from app.domain.configuration_changes import ChangeAction, Usage
from app.domain.configuration_tree import Entity
from app.domain.errors import DomainError, ErrorCode
from tests.support.flow_documents import (
    acme,
    advies,
    document_json,
    draft,
    empty_flows,
    flow,
    flows_of,
    full,
    group,
    installation,
    organisation,
    plan,
    pool,
    published,
    refusals,
    revision,
    skill,
)

FLOW = "organisations[acme].groups[balie].flows[advies]"
APPOINTMENT = "organisations[acme].groups[balie].flows[appointment]"


# --- references -------------------------------------------------------------------------


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
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "legacy_pool_of")
    ]


def test_a_flow_cannot_use_a_skill_that_only_another_organisation_has() -> None:
    data = document_json()
    draft(data)["required_skills"] = ["id-card"]  # beta's skill

    result = plan(data, empty_flows())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.CONFIGURATION_OBJECT_MISSING, "required_skill_ids")
    ]


def test_a_draft_of_a_flow_the_installation_has_cannot_offer_a_subject_the_service_lacks() -> None:
    data = document_json()
    draft(data)["subjects"] = {"rijbewijs": ["passport"]}

    result = plan(data, full())

    assert refusals(result) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "subject_skill_ids")
    ]


def test_a_restored_flow_keeps_every_subject_it_was_recorded_with() -> None:
    data = document_json()
    for found in advies(data)["revisions"]:
        found["subjects"] = {"rijbewijs": ["passport"]}  # published or draft, the service lost it

    assert plan(data, empty_flows()).ok


def test_a_subject_code_of_a_restored_flow_still_has_to_be_a_code() -> None:
    data = document_json()
    advies(data)["revisions"][0]["subjects"] = {"Not A Code": ["passport"]}

    assert plan(data, empty_flows()).refusals


def test_a_later_revision_of_a_stored_flow_cannot_name_what_the_current_one_lacks() -> None:
    stored = flow("advies", "archived", 1, None, [], revision(1, required_skills=["passport"]))
    data = document_json()
    advies(data)["revisions"][1]["subjects"] = {"rijbewijs": ["passport"]}
    advies(data)["draft_revision"] = None
    advies(data)["current_revision"] = 2
    advies(data)["revisions"].pop()

    assert refusals(plan(data, full(stored))) == [
        (f"{FLOW}.revisions[2]", ErrorCode.INVALID_CONFIGURATION, "subject_skill_ids")
    ]


def test_a_later_revision_of_a_restored_flow_may_name_what_the_one_before_it_did_not() -> None:
    data = document_json()
    advies(data)["revisions"][1]["subjects"] = {"rijbewijs": ["passport"]}

    assert plan(data, empty_flows()).ok


def archived_pool(data: dict[str, Any]) -> None:
    """Add the pool ``oud``, archived since the flow's revisions were published."""
    acme(data)["capacity_pools"].append(
        {"code": "oud", "name": "Oud", "guaranteed_skills": [], "archived": True}
    )


def test_a_new_pool_cannot_take_the_name_of_a_pool_hidden_for_lack_of_a_code() -> None:
    data = document_json()
    acme(data)["capacity_pools"].append(
        {"code": "extra", "name": " LOKET 2 ", "guaranteed_skills": [], "archived": False}
    )
    hidden = Usage(hidden_pool_names={"acme": frozenset({"Loket 2"})})

    assert refusals(plan(data, full(), usage=hidden)) == [
        ("organisations[acme].capacity_pools[extra]", ErrorCode.SLUG_TAKEN, "pool_name")
    ]
    assert plan(data, full()).ok  # nothing hidden, so the name is free
    assert plan(data, full(), usage=Usage(hidden_pool_names={"beta": frozenset({"Loket 2"})})).ok


def test_a_restored_pool_may_still_guarantee_a_skill_archived_since() -> None:
    data = document_json()
    acme(data)["capacity_pools"][0]["guaranteed_skills"] = ["passport", "old"]  # "old" is archived

    assert plan(data, empty_flows()).ok


def test_a_restore_keeps_a_published_revision_two_that_requires_a_skill_archived_since() -> None:
    data = document_json()
    advies(data)["revisions"][1]["required_skills"] = ["old"]  # revision 2, published

    assert plan(data, empty_flows()).ok


def test_a_restore_keeps_a_draft_that_requires_a_skill_archived_since() -> None:
    data = document_json()
    draft(data)["required_skills"] = ["old"]

    assert plan(data, empty_flows()).ok


def test_a_restore_keeps_a_current_revision_on_a_pool_archived_since() -> None:
    data = document_json()
    archived_pool(data)
    advies(data)["revisions"][1]["capacity_pool"] = "oud"  # the current revision

    assert plan(data, empty_flows()).ok


def test_a_restore_keeps_a_draft_on_a_pool_archived_since() -> None:
    data = document_json()
    archived_pool(data)
    draft(data)["capacity_pool"] = "oud"

    assert plan(data, empty_flows()).ok


def test_a_draft_of_a_flow_the_installation_has_cannot_newly_choose_an_archived_pool() -> None:
    data = document_json()
    archived_pool(data)
    draft(data)["capacity_pool"] = "oud"
    stored = full()
    stored.children["organisations"][0].children["capacity_pools"] += (pool("oud", archived=True),)

    assert refusals(plan(data, stored)) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "capacity_pool_id")
    ]


def test_a_restore_still_refuses_a_pool_the_tree_does_not_hold() -> None:
    data = document_json()
    draft(data)["capacity_pool"] = "elders"
    assert refusals(plan(data, empty_flows())) == [
        (f"{FLOW}.revisions[3]", ErrorCode.CONFIGURATION_OBJECT_MISSING, "capacity_pool_id")
    ]


def test_a_restore_still_refuses_an_unpublished_revision_that_is_not_the_draft() -> None:
    data = document_json()
    advies(data)["draft_revision"] = None
    advies(data)["current_revision"] = 2

    assert (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "published_at") in refusals(
        plan(data, empty_flows())
    )


def test_an_unpublished_revision_left_behind_by_a_draft_that_moved_on_is_refused() -> None:
    """Apply deletes it, so the plan refuses the document instead of hiding the deletion."""
    data = document_json()
    advies(data)["revisions"].append({**copy.deepcopy(advies(data)["revisions"][2]), "revision": 4})
    advies(data)["draft_revision"] = 4

    assert (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "published_at") in refusals(
        plan(data, full())
    )


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
    advies(data)["revisions"].pop()  # no draft is left either

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


def test_a_revision_draws_on_the_legacy_pool_of_its_own_service_and_no_other() -> None:
    data = document_json()
    flows_of(data)[0]["revisions"][0]["legacy_pool_of"] = "kantoor"

    result = plan(data, empty_flows())

    assert (
        f"{APPOINTMENT}.revisions[1]",
        ErrorCode.INVALID_CONFIGURATION,
        "legacy_pool_of",
    ) in refusals(result)


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


# --- a pool cannot stop guaranteeing what a flow on it requires -----------------------------


def requiring(state: str = "draft") -> Entity:
    """An installation whose flow ``advies`` draws on the pool ``loket`` and requires a skill."""
    advies_flow = flow("advies", state, 1, None, [], revision(1, required_skills=["passport"]))
    return installation(
        organisation(
            "acme",
            group("balie", legacy="Balie", flows=(advies_flow,)),
            skills=(skill("passport"), skill("other")),
            capacity_pools=(pool("loket", "passport", "other"),),
        )
    )


def shrinking(*kept: str) -> dict[str, Any]:
    """A document that makes the pool ``loket`` guarantee only ``kept``."""
    return {
        "format": "openplanner.config/v1",
        "organisations": [
            {"slug": "acme", "capacity_pools": [{"code": "loket", "guaranteed_skills": list(kept)}]}
        ],
    }


def test_a_pool_cannot_stop_guaranteeing_a_skill_a_flow_that_is_not_archived_requires() -> None:
    result = plan(shrinking("other"), requiring())

    assert refusals(result) == [
        (
            "organisations[acme].capacity_pools[loket]",
            ErrorCode.CONFIGURATION_OBJECT_IN_USE,
            "guaranteed_skill_ids",
        )
    ]


def test_a_pool_may_drop_what_no_flow_requires_and_what_an_archived_flow_required() -> None:
    assert plan(shrinking("passport"), requiring()).ok
    assert plan(shrinking("other"), requiring("archived")).ok


# --- the legacy flag -------------------------------------------------------------------------


def legacy_flow(data: dict[str, Any], group_index: int = 0) -> dict[str, Any]:
    """Return the flow ``appointment`` of a group of the exported document."""
    return flows_of(data, 0, group_index)[0]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"required_skills": ["passport"]}, "legacy"),
        ({"intake_modes": ["on_demand"]}, "legacy"),
        ({"interaction_kind": "telephone", "handling_action": "call"}, "legacy"),
        ({"completion": "operator"}, "legacy"),
        ({"collects_email": False}, "legacy"),
        ({"published_at": None}, "current_revision"),
    ],
)
def test_a_legacy_revision_that_is_not_the_wrapper_is_refused(
    changes: dict[str, Any], field: str
) -> None:
    data = document_json()
    legacy_flow(data)["default_for"] = []  # so only the revision can be at fault
    legacy_flow(data)["revisions"][0].update(changes)

    result = plan(data, empty_flows())

    assert field in {r.field for r in result.refusals}


def test_the_flag_is_refused_on_a_flow_that_is_neither_appointment_nor_direct() -> None:
    data = document_json()
    other = copy.deepcopy(legacy_flow(data))
    other["code"] = "advies2"
    other["default_for"] = []
    flows_of(data).append(other)

    assert refusals(plan(data, empty_flows())) == [
        (
            "organisations[acme].groups[balie].flows[advies2].revisions[1]",
            ErrorCode.INVALID_CONFIGURATION,
            "legacy",
        )
    ]


def test_the_flag_is_refused_on_a_later_revision_and_on_a_draft() -> None:
    data = document_json()
    revisions = legacy_flow(data)["revisions"]
    later = {**copy.deepcopy(revisions[0]), "revision": 2, "published_at": published(2)}
    revisions.append(later)
    legacy_flow(data)["current_revision"] = 2

    assert (
        f"{APPOINTMENT}.revisions[2]",
        ErrorCode.INVALID_CONFIGURATION,
        "legacy",
    ) in refusals(plan(data, empty_flows()))

    data = document_json()
    revisions = legacy_flow(data)["revisions"]
    draft_copy = {**copy.deepcopy(revisions[0]), "revision": 2, "published_at": None}
    revisions.append(draft_copy)
    legacy_flow(data)["draft_revision"] = 2

    assert (
        f"{APPOINTMENT}.revisions[2]",
        ErrorCode.INVALID_CONFIGURATION,
        "legacy",
    ) in refusals(plan(data, empty_flows()))


def test_a_legacy_snapshot_may_differ_from_what_the_service_says_now() -> None:
    data = document_json()
    revision_json = legacy_flow(data)["revisions"][0]
    revision_json.update({"labels": {"nl": "Oud", "en": "Old"}, "duration_minutes": 45})

    assert plan(data, empty_flows()).ok


def test_a_legacy_pool_is_used_only_by_the_flows_of_its_own_service_even_without_the_flag() -> None:
    data = document_json()
    other = flows_of(data, 0, 1)[0]["revisions"][0]
    other["capacity_pool"] = None
    other["legacy_pool_of"] = "balie"  # the legacy pool of another service of the organisation

    assert (
        "organisations[acme].groups[kantoor].flows[telefoon].revisions[1]",
        ErrorCode.INVALID_CONFIGURATION,
        "legacy_pool_of",
    ) in refusals(plan(data, empty_flows()))


# --- publishing through a document -----------------------------------------------------------


def next_revision(data: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Publish the draft of ``advies`` (3) and add a revision 4 after it; return revision 4."""
    advies_json = advies(data)
    advies_json["revisions"][2]["published_at"] = published(3)
    fresh = {
        **copy.deepcopy(advies_json["revisions"][1]),
        "revision": 4,
        "published_at": published(4),
        **changes,
    }
    advies_json["revisions"].append(fresh)
    advies_json["current_revision"], advies_json["draft_revision"] = 4, None
    return fresh


def test_a_stored_draft_may_gain_a_publication_time_and_become_the_current_revision() -> None:
    data = document_json()
    advies(data)["revisions"][2]["published_at"] = published(3)
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None

    result = plan(data, full())

    assert result.ok
    assert [(c.kind, c.action, c.key) for c in result.changes if c.kind == "flow_revision"] == [
        ("flow_revision", ChangeAction.UPDATE, ("3",))
    ]


def test_a_publication_time_cannot_lie_in_the_future() -> None:
    data = document_json()
    advies(data)["revisions"][2]["published_at"] = "2027-01-01T10:00:00+00:00"
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None

    assert refusals(plan(data, full())) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "published_at")
    ]


def test_a_revision_is_published_later_than_every_revision_before_it() -> None:
    data = document_json()
    advies(data)["revisions"][2]["published_at"] = advies(data)["revisions"][1]["published_at"]
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None

    assert refusals(plan(data, full())) == [
        (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "published_at")
    ]


def test_revisions_follow_each_other_without_a_gap() -> None:
    data = document_json()
    advies(data)["revisions"][2]["published_at"] = published(3)
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None
    advies(data)["revisions"].append(
        {**copy.deepcopy(advies(data)["revisions"][2]), "revision": 5, "published_at": published(5)}
    )

    assert (FLOW, ErrorCode.INVALID_CONFIGURATION, "revision") in refusals(plan(data, full()))

    data = document_json()
    next_revision(data)
    assert plan(data, full()).ok  # 4 follows 3: in order, no gap


def test_a_published_revision_cannot_newly_name_what_the_one_before_it_did_not_have() -> None:
    data = document_json()
    next_revision(data, required_skills=["old"])  # archived, and revision 3 does not require it

    assert (
        f"{FLOW}.revisions[4]",
        ErrorCode.INVALID_CONFIGURATION,
        "required_skill_ids",
    ) in refusals(plan(data, full()))


def test_a_published_revision_may_keep_what_the_one_before_it_has() -> None:
    data = document_json()
    next_revision(data, required_skills=["passport"], subjects={"paspoort": ["passport"]})
    advies(data)["revisions"][2]["required_skills"] = ["passport"]
    advies(data)["revisions"][2]["subjects"] = {"paspoort": ["passport"]}

    assert refusals(plan(data, full())) == []


def test_publishing_may_not_drop_a_default_entry_path() -> None:
    data = document_json()
    advies(data)["revisions"][2]["published_at"] = published(3)
    advies(data)["revisions"][2]["intake_modes"] = ["on_demand"]
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None
    advies(data)["default_for"] = ["appointment"]

    result = plan(data, full())

    assert {r.field for r in result.refusals} & {"default_for"}


def test_a_history_restored_in_ascending_order_is_checked_revision_by_revision() -> None:
    assert plan(document_json(), empty_flows()).ok  # the round trip of the whole fixture

    data = document_json()
    advies(data)["revisions"][1]["published_at"] = advies(data)["revisions"][0]["published_at"]

    assert (f"{FLOW}.revisions[2]", ErrorCode.INVALID_CONFIGURATION, "published_at") in refusals(
        plan(data, empty_flows())
    )


# --- a flow with history is archived, never deleted ------------------------------------------

KEPT = Usage(
    kept={
        "service_flow": frozenset(
            {
                ("acme", "balie", "advies"),
                ("acme", "balie", "appointment"),
                ("acme", "kantoor", "telefoon"),
            }
        )
    }
)


def omitting(*codes: str) -> dict[str, Any]:
    """A replace document that lists the flows of both groups of ``full`` except ``codes``."""
    data = document_json()
    data["mode"] = "replace"
    for group_json in data["organisations"][0]["groups"]:
        group_json["flows"] = [f for f in group_json["flows"] if f["code"] not in codes]
    return data


def test_a_replace_document_archives_a_flow_that_has_a_published_revision() -> None:
    result = plan(omitting("advies", "telefoon"), full(), usage=KEPT)

    actions = {(c.kind, "/".join(c.address)): c.action for c in result.changes}
    assert result.ok
    # ``advies`` is archived already, so omitting it changes nothing; ``telefoon`` is archived.
    assert actions == {("service_flow", "acme/kantoor/telefoon"): ChangeAction.ARCHIVE}


def test_an_explicit_delete_of_a_flow_with_history_is_an_archive_shown_in_the_plan() -> None:
    data = document_json()
    flows_of(data, 0, 1)[0].clear()
    flows_of(data, 0, 1)[0].update({"code": "telefoon", "action": "delete"})

    result = plan(data, full(), usage=KEPT)

    assert result.ok
    (change,) = result.changes
    assert (change.kind, change.action, change.fields) == (
        "service_flow",
        ChangeAction.ARCHIVE,
        ("state",),
    )
    assert "archive service_flow acme/kantoor/telefoon: state" in result.lines()


def test_a_flow_with_nothing_but_a_draft_is_deleted() -> None:
    only_draft = installation(
        organisation(
            "acme",
            group(
                "balie",
                legacy="Balie",
                flows=(flow("nieuw", "draft", None, 1, [], revision(1, published_at=None)),),
            ),
            capacity_pools=(pool("loket"),),
        )
    )
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "groups": [{"slug": "balie", "flows": [{"code": "nieuw", "action": "delete"}]}],
            }
        ],
    }

    result = plan(data, only_draft)

    assert [(c.kind, c.action) for c in result.changes] == [("service_flow", ChangeAction.DELETE)]


def test_a_flow_that_was_never_published_and_is_still_referred_to_is_not_archived() -> None:
    referred = installation(
        organisation(
            "acme",
            group(
                "balie",
                legacy="Balie",
                flows=(flow("nieuw", "draft", None, 1, [], revision(1, published_at=None)),),
            ),
            capacity_pools=(pool("loket"),),
        )
    )
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "groups": [{"slug": "balie", "flows": [{"code": "nieuw", "action": "delete"}]}],
            }
        ],
    }

    result = plan(
        data, referred, usage=Usage(kept={"service_flow": frozenset({("acme", "balie", "nieuw")})})
    )

    assert refusals(result) == [
        (
            "organisations[acme].groups[balie].flows[nieuw]",
            ErrorCode.CONFIGURATION_DRAFT_FLOW_IN_USE,
            "draft_revision",
        )
    ]


# --- cost ------------------------------------------------------------------------------------


def test_the_world_of_a_resolution_is_read_once_whatever_the_number_of_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []
    original = rules._read_world

    def counting(root: Entity) -> Any:
        reads.append(1)
        return original(root)

    monkeypatch.setattr(rules, "_read_world", counting)

    result = plan(document_json(), empty_flows())

    assert result.ok
    assert reads == [1]
