"""The contact-center code of a skill in a configuration document (ADR 0121 step 4b, A29).

The code is one optional field of the skill: its grammar is checked as the screen checks it, two
skills of one organisation cannot share one, and two organisations may use the same.
"""

from app.domain.errors import ErrorCode
from tests.support.flow_documents import acme, document_json, empty_flows, plan, refusals


def _skills(data: dict[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = acme(data)["skills"]
    return found


def test_a_code_of_the_routing_grammar_is_accepted() -> None:
    data = document_json()
    _skills(data)[0]["contact_center_code"] = "lang_de"

    assert refusals(plan(data, empty_flows())) == []


def test_a_code_that_is_no_code_is_refused() -> None:
    data = document_json()
    _skills(data)[0]["contact_center_code"] = "met spatie"

    assert (
        f"organisations[acme].skills[{_skills(data)[0]['code']}]",
        ErrorCode.INVALID_CONFIGURATION,
        "contact_center_code",
    ) in refusals(plan(data, empty_flows()))


def test_two_skills_of_one_organisation_cannot_share_a_code() -> None:
    data = document_json()
    for entry in _skills(data)[:2]:
        entry["contact_center_code"] = "shared"

    found = refusals(plan(data, empty_flows()))

    assert {(code, field) for _, code, field in found} == {
        (ErrorCode.SLUG_TAKEN, "contact_center_code")
    }
