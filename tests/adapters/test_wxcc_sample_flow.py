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

ADDRESS = "videoafspraak@example.video"
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


def _graph(flow: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    """Return the activities by name and the link targets by (source name, port)."""
    by_id = flow["process"]["activities"]
    nodes = {a["name"]: a["properties"] for a in by_id.values()}
    edges = {
        (by_id[link["sourceActivityId"]]["name"], link["conditionExpr"]): by_id[
            link["targetActivityId"]
        ]["name"]
        for link in flow["process"]["links"]
    }
    return nodes, edges


def test_probe_ends_before_any_queue_and_every_branch_reaches_a_queue() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback", {"toeslagen": "id-2"})
    nodes, edges = _graph(flow)

    assert nodes["CaseKind"]["menuLinks_input"] == ["probe"]
    assert nodes[edges[("CaseKind", "0")]]["activityName"] == "end"
    assert nodes[edges[("CaseKind", "default")]]["activityName"] == "screen-pop"
    for i, route in enumerate(ROUTES):
        queue = nodes[edges[("CaseGroup", str(i))]]
        assert (queue["destination:name"], queue["channelType"]) == (route.queue, "WORK_ITEM")
    assert nodes["Queue_1"]["destination"] == "id-2"
    assert nodes["Queue_0"]["destination"] is None
    assert nodes[edges[("CaseGroup", "default")]]["destination:name"] == "ORG_fallback"


def test_every_activity_and_link_is_on_the_canvas() -> None:
    # A file without its diagram imports as an empty canvas (seen 2026-11-02).
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback")
    widgets = flow["diagram"]["widgets"]
    activities = flow["process"]["activities"]

    assert set(widgets) == set(activities) | {link["id"] for link in flow["process"]["links"]}
    wired = {p["linkId"] for w in widgets.values() for p in w.get("ports", []) if p["linkId"]}
    assert wired == {w["id"] for w in widgets.values() if w["widgetType"] == "link"}


def test_the_sample_flow_reads_routing_and_meeting_link_and_carries_no_participant_data() -> None:
    flow = sample_flow("openplanner_aangifte", ROUTES, "ORG_fallback")
    nodes, _ = _graph(flow)

    assert [v["name"] for v in flow["variables"]] == ["openplannerGroup"]
    assert nodes["CaseGroup"]["expression"] == "{{openplannerGroup}}"
    assert nodes["ScreenPopMeeting"]["screenPopUrl"].endswith(".meetingLink}}")
    assert "firstName" not in str(flow)


def test_a_routing_value_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        sample_flow("x", [Route("a", "q"), Route("a", "r")], "q")


def test_the_flow_names_its_channel_and_a_valid_flow_name() -> None:
    # Flow Designer refused the queues without the channel, and a name with a hyphen (2026-11-02).
    flow = sample_flow("openplanner_acme-groep1", ROUTES, "q", channel=("ch-9", "videoafspraak"))

    assert flow["associatedChannels"] == [
        {"channelType": "workItem", "id": "ch-9", "name": "videoafspraak"}
    ]
    assert flow["name"] == "openplanner_acme_groep1"
