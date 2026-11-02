"""``wxcc-sample-flow``: a Flow Designer flow that routes OpenPlanner's work items (importable).

The flow is written in Flow Designer's FlowV2 format (``schemaVersion`` 2.0), the format the
configuration API exports (``v2/flows/{id}:export``) and ``wxcc_config.parse_flow`` reads;
Flow Designer's server resolves activity, event and queue ids from the names on import. It
holds what the runbook asks of a flow (runbook section 3, ``docs/wxcc-sample-flow.md``):

    NewContact ─► Case kind ─ probe ──► End Flow                      (ADR 0082 probe)
                            ├ direct ─► Set Contact Priority ─┐
                            └ default ────────────────────────┴─► Screen Pop meetingLink
    Screen Pop ─► Case openplannerGroup ─ <value> ─► Queue Contact <queue> ─► End Flow
                                        └ default ─► Queue Contact <fallback queue> ─► End Flow

``openplannerGroup`` is a flow variable that OpenPlanner fills through ``flowSettings``;
``kind`` and ``meetingLink`` come from the work item data in the start node's media payload.
Queues are named, not ids: the admin imports the flow and picks the queues it names. The
flow carries routing values and queue names only, never participant data.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.adapters.wxcc import PROBE_KIND, ROUTING_KEY

WORK_ITEM_DATA = "NewContact.MediaPayload.workItemData"
"""Where a flow reads the work item's data: the start node's media payload (Cisco's Work Item
guide, "Flow Support"). The exact path is checked live by the group check's probe: a probe
that is not ended by the flow means the ``kind`` expression does not resolve."""
DIRECT_KIND = "direct"
"""``kind`` of a direct call (ADR 0072); it gets ``DIRECT_PRIORITY``."""
DIRECT_PRIORITY = 1
"""Contact priority of a direct call: P1, ahead of scheduled work items (Flow Designer's 1–9)."""
SCREEN_POP_LABEL = "Videogesprek"
"""Label of the Screen Pop on Agent Desktop: the display name of ``meetingLink`` (runbook)."""
WORK_ITEM_CHANNEL = "WORK_ITEM"
EVENTS = ("AgentContactAssigned", "AgentOfferContact", "ContactEnded", "GlobalErrorHandling")
"""Event handlers Flow Designer adds to every new flow; the sample keeps them empty."""


@dataclass(frozen=True)
class Route:
    """One Case branch: a routing value and the queue its work items go to."""

    value: str
    queue: str


def _node(name: str, activity: str, **properties: Any) -> dict[str, Any]:
    """Return a FlowV2 action node."""
    return {
        "id": name,
        "name": name,
        "activityType": "action",
        "properties": {"name": name, "activityName": activity, **properties},
    }


def _edge(source: str, target: str, condition: str | None) -> dict[str, Any]:
    """Return a FlowV2 edge on one of ``source``'s output ports (``None``: its success path).

    Set Contact Priority lists only ``failure`` in the activity registry; Flow Designer's
    validator accepts its success edge only without a condition.
    """
    edge: dict[str, Any] = {"from": source, "to": target, "properties": {}}
    if condition is not None:
        edge["condition"] = condition
    return edge


def _case(name: str, expression: str, values: Sequence[str]) -> dict[str, Any]:
    """Return a Case node with one numbered branch per value (ports ``0``, ``1``, …).

    The shape of Cisco's own exported flows: ``menuLinks`` numbers the branches, the
    ``menuLinks_input`` pair holds the values, and edges leave on ``0``, ``1``, … and
    ``default``. Flow Designer's dry-run validator knows only the ``error`` port of a Case.
    """
    return _node(
        name,
        "case-statement",
        expression=expression,
        menuLinks=[str(i) for i in range(len(values))],
        menuLinks_input=list(values),
        **{"menuLinks:input": list(values)},
    )


def _queue(name: str, queue: str) -> dict[str, Any]:
    """Return a Queue Contact node on a Work Item queue named ``queue``."""
    return _node(name, "queue-contact", queueId=queue, channelType=WORK_ITEM_CHANNEL)


def _event(name: str) -> dict[str, Any]:
    """Return an empty event handler node (ids resolved by Flow Designer)."""
    return {
        "id": name,
        "name": name,
        "activityType": "event",
        "properties": {
            "name": name,
            "activityName": "Event",
            "eventSourceName": "WebexContactCenter",
            "eventClassificationName": "VoiceInteractions",
            "eventSpecificationName": name,
        },
    }


def sample_flow(name: str, routes: Sequence[Route], fallback_queue: str) -> dict[str, Any]:
    """Return the sample flow for ``routes``, with ``fallback_queue`` on the default branch.

    Args:
        name: Flow name in Flow Designer (unique in the organisation).
        routes: One Case branch per routing value, in order; values must be unique.
        fallback_queue: Queue of the default branch (a routing value without a branch).

    Raises:
        ValueError: when a routing value occurs twice.
    """
    values = [route.value for route in routes]
    if len(set(values)) != len(values):
        raise ValueError("duplicate routing value")
    start = {
        "id": "NewContact",
        "name": "NewContact",
        "activityType": "start",
        "properties": {
            "name": "NewContact",
            "activityName": "NewContact",
            "event": "NewContact",
            "flowType": {
                "eventSourceName": "WebexContactCenter",
                "eventClassificationName": "VoiceInteractions",
                "eventSpecificationName": "ContactStartWorkflow",
            },
        },
    }
    nodes = [
        start,
        _case("CaseKind", f"{{{{{WORK_ITEM_DATA}.kind}}}}", [PROBE_KIND, DIRECT_KIND]),
        _node("EndProbe", "end"),
        _node(
            "PriorityDirect",
            "set-contact-priority",
            priorityRadioGroup="staticPriority",
            priority=DIRECT_PRIORITY,
        ),
        _node(
            "ScreenPopMeeting",
            "screen-pop",
            screenPopUrl=f"{{{{{WORK_ITEM_DATA}.meetingLink}}}}",
            screenPopDesktopLabel=SCREEN_POP_LABEL,
            target="outsideDesktop",
            queryParameters={},
        ),
        _case("CaseGroup", f"{{{{{ROUTING_KEY}}}}}", values),
        *[_queue(f"Queue_{i}", route.queue) for i, route in enumerate(routes)],
        _queue("QueueDefault", fallback_queue),
        _node("EndFlow", "end"),
    ]
    edges = [
        _edge("NewContact", "CaseKind", "out"),
        _edge("CaseKind", "EndProbe", "0"),
        _edge("CaseKind", "PriorityDirect", "1"),
        _edge("CaseKind", "ScreenPopMeeting", "default"),
        _edge("CaseKind", "ScreenPopMeeting", "error"),
        _edge("PriorityDirect", "ScreenPopMeeting", None),
        _edge("PriorityDirect", "ScreenPopMeeting", "failure"),
        _edge("ScreenPopMeeting", "CaseGroup", "out"),
        *[_edge("CaseGroup", f"Queue_{i}", str(i)) for i in range(len(routes))],
        _edge("CaseGroup", "QueueDefault", "default"),
        _edge("CaseGroup", "QueueDefault", "error"),
        *[_edge(f"Queue_{i}", "EndFlow", "default") for i in range(len(routes))],
        _edge("QueueDefault", "EndFlow", "default"),
    ]
    return {
        "schemaVersion": "2.0",
        "name": name,
        "description": "OpenPlanner work items: probe, priority, Screen Pop, queue per group",
        "flowType": "FLOW",
        "contactType": "workItem",
        "variables": [
            {
                "name": ROUTING_KEY,
                "type": "STRING",
                "value": "",
                "description": "Routing value of the OpenPlanner group (flowSettings)",
                "isCAD": False,
                "isAgentEditable": False,
                "isReportable": True,
                "isSecure": False,
                "isExternalized": False,
                "desktopLabel": "",
            }
        ],
        "nodes": nodes,
        "edges": edges,
        "eventFlows": {"nodes": [_event(event) for event in EVENTS], "edges": []},
    }
