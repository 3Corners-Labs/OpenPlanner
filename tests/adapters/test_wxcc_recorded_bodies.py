"""The WxCC request bodies stay byte for byte what they were (ADR 0121 step 6).

``wxcc_task_bodies.json`` was recorded from ``app/adapters/wxcc.py`` before the
channel-independent task contract existed (``main`` at 18675652): one body per kind of task
OpenPlanner sends (a scheduled and a direct booking, one with routing variables, one with skill
codes, the probe of a group check, the cancellation message). Each body is serialised the way
``httpx`` sends a ``json=`` body (no spaces, UTF-8, key order kept), so a changed key, value,
order or escape fails here. A deliberate change to the payload re-records the file in the same
PR and says so (``docs/webhooks.md``).
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.adapters.wxcc import cancellation_body, task_body
from app.domain.calls import ContactCenterTask, probe_task
from app.domain.interaction import InteractionKind
from app.domain.ports import IntegrationError
from tests.support.domain import make_group, webex_routing

RECORDED = Path(__file__).with_name("wxcc_task_bodies.json")
ALIAS = "alias-1"
STAMP = 1767600000000

BASE = ContactCenterTask(
    reference="OP-000123",
    first_name="Zoë",
    start_label="5 jan 2026, 09:00",
    group_name="Demo: videogesprek",
    meeting_link="https://site.webex.com/m/1?launchApp=true",
    kind="scheduled",
    routing=webex_routing("openplanner@example.biz", "openplanner-video"),
    routing_value="demo",
)
GROUP = make_group(
    slug="demo",
    name="Demo",
    contact_center=webex_routing("openplanner@example.biz", "openplanner-video", "demo-route"),
)


def _bodies() -> dict[str, dict[str, Any]]:
    """Return every recorded case's request body, built by the adapter as it is now."""
    probe = probe_task(
        GROUP,
        reference="CHECK-1",
        first_name="Check",
        start_label="5 jan 2026, 09:00",
        meeting_link="https://site.webex.com/m/probe",
    )
    return {
        "scheduled": task_body(BASE, ALIAS, STAMP),
        "direct": task_body(replace(BASE, kind="direct"), ALIAS, STAMP),
        "routing_variables": task_body(
            replace(BASE, variables=(("lang", "nl"), ("topic", "MOVE"))), ALIAS, STAMP
        ),
        "skills": task_body(
            replace(BASE, variables=(("topic", "MOVE"),), skills=("intake_1", "lang_de")),
            ALIAS,
            STAMP,
        ),
        "probe": task_body(probe, ALIAS, STAMP),
        "cancellation": cancellation_body("OP-000123", ALIAS, STAMP),
    }


def _wire(body: dict[str, Any]) -> str:
    """Return ``body`` as ``httpx`` puts a ``json=`` body on the wire."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _recorded() -> dict[str, str]:
    """Return the recorded wire form of every case."""
    loaded: dict[str, str] = json.loads(RECORDED.read_text(encoding="utf-8"))
    return loaded


def test_every_case_is_recorded() -> None:
    assert set(_recorded()) == set(_bodies())


@pytest.mark.parametrize("case", sorted(_bodies()))
def test_the_body_is_byte_for_byte_the_recorded_one(case: str) -> None:
    assert _wire(_bodies()[case]).encode() == _recorded()[case].encode()


@pytest.mark.parametrize(
    "interaction", [k for k in InteractionKind if k is not InteractionKind.VIDEO]
)
def test_a_task_wxcc_does_not_carry_is_refused_before_it_is_sent(
    interaction: InteractionKind,
) -> None:
    with pytest.raises(IntegrationError, match="not_ready"):
        task_body(replace(BASE, interaction=interaction), ALIAS, STAMP)
