"""Inputs shared by the skill, capacity and staff skill screens (ADR 0121)."""

import pytest
from starlette.requests import Request

from app.admin.forms import FormData, done_message
from app.admin.skill_inputs import chosen_ids, is_unknown, ticked_ids
from app.domain.errors import DomainError, ErrorCode


def form(*names: str) -> FormData:
    return FormData(dict.fromkeys(names, "on"), lambda key, **_: key)


def test_the_ticked_skills_are_the_well_formed_inputs_only() -> None:
    crafted = ("skill-\u00b2", "skill-\u0663", "skill--1", "skill-+1", "skill- 1", "skill-1_0")
    ticked = form("skill-1", "skill-22", "skill-x", "skill-", "other", "skill-1234567890", *crafted)

    assert ticked_ids(ticked) == {1, 22}


def test_a_change_acts_on_at_most_twice_the_skill_limit_in_ids() -> None:
    ticked = form("skill-1", "skill-2", "skill-3", "skill-4", "skill-5")

    assert chosen_ids(ticked, 3) == {1, 2, 3, 4, 5}
    with pytest.raises(DomainError) as refused:
        chosen_ids(ticked, 2)
    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "skill_ids",
    )


@pytest.mark.parametrize(
    ("error", "unknown"),
    [
        (DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id"), True),
        (DomainError(ErrorCode.STAFF_NOT_FOUND), True),
        (DomainError(ErrorCode.INVALID_CONFIGURATION, "mode"), True),  # a legacy pool
        (DomainError(ErrorCode.INVALID_CONFIGURATION, "pool_name"), False),
        (DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool"), False),
    ],
)
def test_what_answers_404(error: DomainError, unknown: bool) -> None:
    assert is_unknown(error) is unknown


def request_for(query: str) -> Request:
    return Request({"type": "http", "query_string": query.encode(), "headers": []})


def test_a_confirmation_can_be_limited_to_the_keys_a_section_shows() -> None:
    def say(key: str, **_: object) -> str:
        return f"<{key}>"

    saved = request_for("done=staff_skills_saved")
    other = request_for("done=staff_suspended")

    assert done_message(saved, say) == "<admin.done.staff_skills_saved>"
    assert done_message(saved, say, only=("staff_skills_saved", "unchanged")) is not None
    assert done_message(other, say) is not None
    assert done_message(other, say, only=("staff_skills_saved", "unchanged")) is None
