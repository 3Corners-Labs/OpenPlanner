"""The skill codes in the Create Task body (ADR 0121 step 4b): one reserved flow variable.

A task that requires skills carries their mapped codes in ``openplannerSkills``; a task that
requires none has no such key, so every legacy payload stays byte for byte what it was; a
routing variable of ADR 0081 with that name cannot stand in for the codes.
"""

from dataclasses import replace

from app.adapters.wxcc import ROUTING_KEY, SKILLS_KEY, task_body
from app.domain.calls import ContactCenterTask
from tests.support.domain import webex_routing

TASK = ContactCenterTask(
    reference="OP-000123",
    first_name="Ada",
    start_label="5 jan 2026, 09:00",
    group_name="Demo",
    meeting_link="https://site.webex.com/meet/abc",
    kind="scheduled",
    routing=webex_routing("openplanner@example.biz", "openplanner-video"),
    routing_value="demo",
    variables=(("topic", "MOVE"),),
)


def _settings(task: ContactCenterTask) -> object:
    return task_body(task, "alias-1", 1)["flowSettings"]


def test_the_codes_travel_in_one_reserved_variable() -> None:
    assert _settings(replace(TASK, skills=("intake_1", "lang_de"))) == {
        "topic": "MOVE",
        SKILLS_KEY: "intake_1,lang_de",
        ROUTING_KEY: "demo",
    }


def test_a_task_without_skills_has_no_skill_key() -> None:
    assert _settings(TASK) == {"topic": "MOVE", ROUTING_KEY: "demo"}


def test_a_routing_variable_cannot_stand_in_for_the_codes() -> None:
    forced = replace(TASK, variables=((SKILLS_KEY, "forged"),), skills=("lang_de",))
    assert _settings(forced) == {SKILLS_KEY: "lang_de", ROUTING_KEY: "demo"}
