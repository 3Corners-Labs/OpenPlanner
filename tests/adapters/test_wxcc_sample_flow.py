"""``wxcc-sample-flow``: the importable flow passes OpenPlanner's own flow check."""

from typing import Any

import pytest

from app.adapters.diagnostics import CheckResult
from app.adapters.wxcc_config import (
    Asset,
    Channel,
    EntryPoint,
    GroupRouting,
    Queue,
    WxccConfig,
    check_groups,
    parse_flow,
)
from app.adapters.wxcc_sample_flow import Route, sample_flow

ADDRESS = "videoafspraak@rijks.video"
CHANNEL = "videoafspraak-prototype"
SCHEMA = {
    key: (key == "bookingReference", True)
    for key in ("bookingReference", "firstName", "startTime", "groupName", "meetingLink", "kind")
}
ROUTES = [Route("aangifte", "ORG_videoafspraak_aangifte_01"), Route("toeslagen", "toeslagen")]


def _config(flow: dict[str, Any]) -> WxccConfig:
    return WxccConfig(
        channels=(Channel("ch-1", CHANNEL, "WORK_ITEM"),),
        assets=(Asset("as-1", CHANNEL, "ch-1", ADDRESS, SCHEMA),),
        entry_points=(EntryPoint("ep-1", "videoafspraak_prototype", "as-1", "fl-1", True),),
        queues=(
            Queue("q-1", "ORG_videoafspraak_aangifte_01", "WORK_ITEM"),
            Queue("q-2", "toeslagen", "WORK_ITEM"),
            Queue("q-3", "telefonie", "TELEPHONY"),
            Queue("q-4", "ORG_fallback", "WORK_ITEM"),
        ),
        flows={"fl-1": parse_flow(flow)},
    )


def _checks(flow: dict[str, Any]) -> list[CheckResult]:
    groups = [GroupRouting(r.value, CHANNEL, ADDRESS, r.value) for r in ROUTES]
    return check_groups(groups, _config(flow))


def test_the_sample_flow_passes_the_flow_check_without_warnings() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback")

    results = _checks(flow)

    assert [r.name for r in results if not r.ok] == []
    names = {r.name.split("] ", 1)[1] for r in results}
    assert {"flow routing", "flow branch", "probe branch", "screen pop", "queue naming"} <= names


def test_a_flow_with_only_one_branch_leaves_the_other_group_on_the_default_branch() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES[:1], "ORG_fallback")

    warnings = [r for r in _checks(flow) if not r.ok]

    assert [(r.name, r.required) for r in warnings] == [
        ("[toeslagen] flow branch", False),
        ("[toeslagen] queue naming", False),
    ]


def test_probe_ends_before_any_queue_and_every_branch_reaches_a_queue() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback")
    nodes = {n["name"]: n["properties"] for n in flow["nodes"]}
    edges = {(e["from"], e.get("condition")): e["to"] for e in flow["edges"]}

    kind = nodes["CaseKind"]
    assert kind["menuLinks_input"][0] == "probe"
    assert nodes[edges[("CaseKind", "0")]]["activityName"] == "end"
    assert kind["menuLinks_input"][1] == "direct"
    assert nodes[edges[("CaseKind", "1")]]["activityName"] == "set-contact-priority"
    for i, route in enumerate(ROUTES):
        queue = nodes[edges[("CaseGroup", str(i))]]
        assert (queue["queueId"], queue["channelType"]) == (route.queue, "WORK_ITEM")
    assert nodes[edges[("CaseGroup", "default")]]["queueId"] == "ORG_fallback"
    assert {e["from"] for e in flow["edges"]} | {e["to"] for e in flow["edges"]} <= set(nodes)


def test_the_sample_flow_reads_routing_and_meeting_link_and_carries_no_participant_data() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback")
    nodes = {n["name"]: n["properties"] for n in flow["nodes"]}

    assert [v["name"] for v in flow["variables"]] == ["openplannerGroup"]
    assert nodes["CaseGroup"]["expression"] == "{{openplannerGroup}}"
    assert nodes["ScreenPopMeeting"]["screenPopUrl"].endswith(".meetingLink}}")
    assert "firstName" not in str(flow)


def test_a_routing_value_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        sample_flow("x", [Route("a", "q"), Route("a", "r")], "q")
