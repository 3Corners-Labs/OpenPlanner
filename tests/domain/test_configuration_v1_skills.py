"""Skills and counted capacity pools in a configuration document (ADR 0121 step 2b-ii)."""

import pytest

from app.domain.configuration_changes import ChangeAction
from app.domain.errors import DomainError, ErrorCode
from tests.support.flow_documents import (
    acme,
    document_json,
    empty_flows,
    full,
    group,
    installation,
    organisation,
    plan,
    pool,
    refusals,
    skill,
)

FLOW = "organisations[acme].groups[balie].flows[advies]"
APPOINTMENT = "organisations[acme].groups[balie].flows[appointment]"


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
            {
                "slug": "acme",
                "capacity_pools": [
                    {"code": "loket", "name": "Loket", "guaranteed_skills": ["old"]}
                ],
            }
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


def test_two_pools_with_one_code_in_an_organisation_are_refused_when_read() -> None:
    data = document_json()
    acme(data)["capacity_pools"].append({"code": "loket", "name": "Other"})

    with pytest.raises(DomainError) as refused:
        plan(data, empty_flows())

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == "organisations[acme].capacity_pools[loket]"


def test_a_new_pool_cannot_take_the_name_of_another_pool_that_is_not_archived() -> None:
    data = document_json()
    acme(data)["capacity_pools"].append({"code": "second", "name": " LOKET "})

    result = plan(data, full())

    assert refusals(result) == [
        ("organisations[acme].capacity_pools[second]", ErrorCode.SLUG_TAKEN, "pool_name")
    ]


def test_a_pool_is_found_by_its_code_so_a_rename_is_an_ordinary_change() -> None:
    data = document_json()
    acme(data)["capacity_pools"][0]["name"] = "Nieuwe naam"

    result = plan(data, full())

    assert result.ok
    (change,) = result.changes
    assert (change.kind, change.action, change.key, change.fields) == (
        "capacity_pool",
        ChangeAction.UPDATE,
        ("loket",),
        ("name",),
    )


def test_a_pool_code_has_the_grammar_of_a_skill_code() -> None:
    data = document_json()
    acme(data)["capacity_pools"][0]["code"] = "Not A Code"

    assert (
        "organisations[acme].capacity_pools[Not A Code]",
        ErrorCode.INVALID_CONFIGURATION,
        "pool_code",
    ) in refusals(plan(data, empty_flows()))


def test_a_revision_names_its_counted_pool_by_code_not_by_name() -> None:
    data = document_json()
    data["organisations"][0]["groups"][1]["flows"][0]["revisions"][0]["capacity_pool"] = "Loket"

    assert (
        "organisations[acme].groups[kantoor].flows[telefoon].revisions[1]",
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "capacity_pool_id",
    ) in refusals(plan(data, empty_flows()))


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
