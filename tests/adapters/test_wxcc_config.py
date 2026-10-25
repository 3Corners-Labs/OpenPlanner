"""``wxcc-config-check``: does WxCC's configuration route each group's work items?

Shapes recorded from the product owner's organisation on 2026-10-25 (eu2): one Work Item
channel, an asset with its own schema, an entry point whose flow queues every contact to one
queue. Names are kept, ids replaced.
"""

from collections.abc import Callable
from typing import Any

import httpx

from app.adapters.diagnostics import CheckResult, format_report, passed
from app.adapters.wxcc_config import (
    PROJECT_ID,
    Asset,
    Channel,
    EntryPoint,
    FlowSummary,
    GroupRouting,
    Queue,
    WxccConfig,
    check_groups,
    fetch_config,
    find_region,
    names_match,
    parse_flow,
)

ORG = "11111111-2222-3333-4444-555555555555"
OPENPLANNER_SCHEMA = {
    "bookingReference": (True, True),
    "firstName": (False, True),
    "startTime": (False, True),
    "groupName": (False, True),
    "meetingLink": (False, True),
    "kind": (False, True),
}
FLOW_DOC: dict[str, Any] = {
    "name": "videoafspraak_prototype",
    "variables": [],
    "nodes": [
        {"name": "NewContact", "properties": {"activityName": "NewContact"}},
        {"name": "Queue", "properties": {"activityName": "queue-contact", "destination": "q-1"}},
        {"name": "End", "properties": {"activityName": "end"}},
    ],
    "edges": [],
}


def _config(
    *,
    schema: dict[str, tuple[bool, bool]] | None = None,
    flow: FlowSummary | None = None,
    queues: tuple[Queue, ...] = (Queue("q-1", "videoafspraak_prototype", "WORK_ITEM"),),
) -> WxccConfig:
    return WxccConfig(
        channels=(Channel("ch-1", "videoafspraak-prototype", "WORK_ITEM"),),
        assets=(
            Asset(
                "as-1",
                "videoafspraak-prototype",
                "ch-1",
                "videoafspraak@rijks.video",
                OPENPLANNER_SCHEMA if schema is None else schema,
            ),
        ),
        entry_points=(EntryPoint("ep-1", "videoafspraak_prototype", "as-1", "fl-1", True),),
        queues=queues,
        flows={"fl-1": flow if flow is not None else parse_flow(FLOW_DOC)},
    )


GROUP = GroupRouting("aangifte", "videoafspraak-prototype", "videoafspraak@rijks.video", "aangifte")


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name.split("] ", 1)[1]: r for r in results}


def test_the_recorded_prototype_asset_schema_is_refused() -> None:
    recorded = {"videoafspraak_Naam": (True, True), "videoafspraak_MeetingURL": (True, True)}

    results = _by_name(check_groups([GROUP], _config(schema=recorded)))

    schema = results["asset schema"]
    assert not schema.ok
    assert schema.required
    assert "missing bookingReference" in schema.hint
    assert "requires videoafspraak_MeetingURL, videoafspraak_Naam" in schema.hint


def test_a_matching_setup_passes_and_names_the_queue() -> None:
    results = check_groups([GROUP], _config())

    by_name = _by_name(results)
    assert by_name["channel"].ok
    assert by_name["asset"].ok
    assert by_name["asset schema"].ok
    assert by_name["entry point"].ok
    assert "videoafspraak_prototype" in by_name["flow queues"].hint
    assert passed(results)  # flow and naming findings only warn for a single group


def test_a_flow_without_the_routing_variable_fails_once_groups_share_the_asset() -> None:
    other = GroupRouting("paspoort", GROUP.channel, GROUP.business_address, "paspoort")

    results = check_groups([GROUP, other], _config())

    routing = _by_name(results[: len(results) // 2])["flow routing"]
    assert not routing.ok
    assert routing.required
    assert "aangifte, paspoort" in routing.hint
    assert not passed(results)


def test_a_routing_flow_needs_a_branch_per_value() -> None:
    doc = {
        **FLOW_DOC,
        "variables": [{"name": "openplannerGroup", "type": "STRING"}],
        "nodes": [
            *FLOW_DOC["nodes"],
            {"name": "Case", "properties": {"activityName": "case", "cases": ["paspoort"]}},
        ],
    }

    results = _by_name(check_groups([GROUP], _config(flow=parse_flow(doc))))

    assert results["flow routing"].ok
    branch = results["flow branch"]
    assert not branch.ok
    assert not branch.required
    assert "aangifte" in branch.hint


def test_missing_channel_asset_and_entry_point_are_failures() -> None:
    wrong = GroupRouting("aangifte", "openplanner-video", "openplanner@example.nl", "aangifte")

    results = _by_name(check_groups([wrong], _config()))

    assert not results["channel"].ok
    assert "videoafspraak-prototype" in results["channel"].hint  # what exists instead
    assert not results["asset"].ok
    assert "videoafspraak@rijks.video" in results["asset"].hint


def test_an_asset_on_another_channel_fails() -> None:
    config = _config()
    moved = WxccConfig(
        channels=(*config.channels, Channel("ch-2", "andere", "WORK_ITEM")),
        assets=(Asset("as-1", "x", "ch-2", "videoafspraak@rijks.video", OPENPLANNER_SCHEMA),),
        entry_points=config.entry_points,
        queues=config.queues,
        flows=config.flows,
    )

    asset = _by_name(check_groups([GROUP], moved))["asset"]

    assert not asset.ok
    assert "on channel andere" in asset.hint


def test_queue_naming_rule_matches_name_parts() -> None:
    assert names_match("aangifte", "FIN-BLD_videoafspraak_aangifte_01")
    assert names_match("video-afspraak", "Video_Afspraak")
    assert not names_match("aangifte", "aangiftes_video")
    assert not names_match("", "anything")


def test_naming_rule_warns_when_several_queues_and_none_named_after_the_group() -> None:
    queues = (
        Queue("q-1", "videoafspraak_prototype", "WORK_ITEM"),
        Queue("q-2", "videoafspraak_paspoort", "WORK_ITEM"),
    )
    doc = {
        **FLOW_DOC,
        "variables": [{"name": "openplannerGroup", "type": "STRING"}],
        "nodes": [
            *FLOW_DOC["nodes"],
            {"name": "Q2", "properties": {"activityName": "queue-contact", "destination": "q-2"}},
            {"name": "Case", "properties": {"activityName": "case", "cases": ["aangifte"]}},
        ],
    }

    naming = _by_name(check_groups([GROUP], _config(flow=parse_flow(doc), queues=queues)))[
        "queue naming"
    ]

    assert not naming.ok
    assert not naming.required
    assert "contains the routing value" in naming.hint


def test_screen_pop_and_non_work_item_queues_are_reported() -> None:
    queues = (Queue("q-1", "videoafspraak_prototype", "CHAT"),)

    results = _by_name(check_groups([GROUP], _config(queues=queues)))

    assert not results["flow queues"].ok
    assert "CHAT" in results["flow queues"].hint
    assert not results["screen pop"].ok
    assert not results["screen pop"].required


def test_flow_summary_reads_variables_activities_and_strings_only() -> None:
    summary = parse_flow(
        {**FLOW_DOC, "variables": [{"name": "openplannerGroup", "value": "secret-ish default"}]}
    )

    assert summary.variables == frozenset({"openplannerGroup"})
    assert summary.activities == frozenset({"NewContact", "queue-contact", "end"})
    assert "q-1" in summary.strings


def _routes(handlers: dict[str, Callable[[httpx.Request], httpx.Response]]) -> httpx.AsyncClient:
    def route(request: httpx.Request) -> httpx.Response:
        for prefix, handler in handlers.items():
            if request.url.host + request.url.path == prefix or (
                request.url.host + request.url.path
            ).startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": {"reason": "nothing here"}})

    return httpx.AsyncClient(transport=httpx.MockTransport(route))


def _unknown_org(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        404, json={"error": {"reason": f"No Organization was found with id {ORG}"}}
    )


async def test_the_region_that_knows_the_organisation_is_found() -> None:
    http = _routes(
        {
            "api.wxcc-eu1.cisco.com/organization/": _unknown_org,
            "api.wxcc-eu2.cisco.com/organization/": lambda r: httpx.Response(
                200, json={"data": []}
            ),
        }
    )

    found = await find_region(http, "token", "https://api.wxcc-eu1.cisco.com", ORG)

    assert found == "https://api.wxcc-eu2.cisco.com"


async def test_the_configured_region_is_kept_when_it_knows_the_organisation() -> None:
    http = _routes(
        {"api.wxcc-eu2.cisco.com/organization/": lambda r: httpx.Response(200, json={"data": []})}
    )

    found = await find_region(http, "token", "https://api.wxcc-eu2.cisco.com", ORG)

    assert found == "https://api.wxcc-eu2.cisco.com"


async def test_config_is_fetched_with_selected_fields_and_no_secrets() -> None:
    seen: list[httpx.Request] = []
    base = f"api.wxcc-eu2.cisco.com/organization/{ORG}"

    def page(items: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": items, "meta": {"totalPages": 1}})

        return handler

    asset = {
        "id": "as-1",
        "name": "videoafspraak-prototype",
        "channelId": "ch-1",
        "businessAddress": "videoafspraak@rijks.video",
        "dataSchema": [{"fieldKey": "bookingReference", "isRequired": True, "isViewable": True}],
        "webhookConfig": {"webhookUrl": "https://x", "webhookSecret": "never-kept"},
    }
    http = _routes(
        {
            f"{base}/v2/channel": page(
                [{"id": "ch-1", "name": "videoafspraak-prototype", "channelType": "WORK_ITEM"}]
            ),
            f"{base}/v2/asset": page([asset]),
            f"{base}/v2/entry-point": page(
                [
                    {
                        "id": "ep-1",
                        "name": "videoafspraak_prototype",
                        "channelType": "WORK_ITEM",
                        "channelAssetId": "as-1",
                        "flowId": "fl-1",
                        "active": True,
                    },
                    {"id": "ep-2", "name": "chat", "channelType": "CHAT"},
                ]
            ),
            f"{base}/v2/contact-service-queue": page(
                [{"id": "q-1", "name": "videoafspraak_prototype", "channelType": "WORK_ITEM"}]
            ),
            f"api.wxcc-eu2.cisco.com/{ORG}/project/{PROJECT_ID}/v2/flows/fl-1:export": (
                lambda r: httpx.Response(200, json=FLOW_DOC)
            ),
        }
    )

    config = await fetch_config(http, "token", "https://api.wxcc-eu2.cisco.com", ORG)

    assert [e.name for e in config.entry_points] == ["videoafspraak_prototype"]
    assert config.assets[0].fields == {"bookingReference": (True, True)}
    assert "never-kept" not in repr(config)
    assert config.flows["fl-1"] is not None
    queue_request = next(r for r in seen if r.url.path.endswith("contact-service-queue"))
    assert queue_request.url.params["attributes"] == "id,name,channelType"  # no agent data


def test_report_lines_carry_the_group() -> None:
    report = format_report(check_groups([GROUP], _config()))

    assert "[aangifte] channel" in report
