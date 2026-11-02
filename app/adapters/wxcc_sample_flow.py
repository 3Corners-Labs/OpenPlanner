"""``wxcc-sample-flow``: a Flow Designer flow that routes OpenPlanner's work items (importable).

The flow is written in the format Flow Designer's **Import** takes: the one its own export
screen writes (``process.activities`` and ``links`` plus ``diagram.widgets`` that place every
activity and link on the canvas; a file without the diagram imports as an empty canvas). The
shape follows a flow exported from the product owner's organisation on 2026-11-02 and Cisco's
published sample flows. It holds what the runbook asks of a flow (``docs/wxcc-sample-flow.md``):

    NewContact ─► Case kind ─ probe ──► End Flow                      (ADR 0082 probe)
                            └ default ─► Screen Pop meetingLink ─► Case openplannerGroup
    Case openplannerGroup ─ <value> ─► Queue Contact <queue> ─► End Flow
                          └ default ─► Queue Contact <fallback queue> ─► End Flow

``openplannerGroup`` is a flow variable that OpenPlanner fills through ``flowSettings``;
``kind`` and ``meetingLink`` come from the work item data in the start node's media payload.
Set Contact Priority for direct calls is left to the admin: no export holds that activity's
id. The flow carries routing values and queue names only, never participant data.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.adapters.wxcc import PROBE_KIND, ROUTING_KEY

WORK_ITEM_DATA = "NewContact.MediaPayload.workItemData"
"""Where a flow reads the work item's data: the start node's media payload (Cisco's Work Item
guide, "Flow Support"). The group check's probe shows whether the path resolves."""
SCREEN_POP_LABEL = "Videogesprek"
"""Label of the Screen Pop on Agent Desktop: the display name of ``meetingLink`` (runbook)."""

START_EVENT = {
    "eventSourceId": "5eff0e2eb10162499bc9f932",
    "eventClassificationId": "5f10475cf021306e89e1692c",
    "eventSpecificationId": "5f2d02144332507e39cc8a7f",
    "eventSourceName": "WebexContactCenter",
    "eventClassificationName": "VoiceInteractions",
    "eventSpecificationName": "ContactStartWorkflow",
}
"""The ids of the start event; the same for every organisation (recorded export)."""
EVENTS = (
    ("GlobalErrorHandling", "OnGlobalError", "627c0663e98eca854178bba6", "icon-warning-bold_20"),
    (
        "AgentOfferContact",
        "AgentOffered",
        "637da544c02b556cf406d192",
        "icon-assign-privilege-bold_20",
    ),
    ("ContactEnded", "ContactEnded", "5f354a336fb8c44e97ccb30e", "icon-cancel-bold_20"),
    (
        "AgentContactAssigned",
        "AgentAccepted",
        "5f354a0c6fb8c44e97ccb30d",
        "icon-assign-host-bold_20",
    ),
)
"""Event handlers every flow carries: (name, display name, specification id, icon)."""

# Layout of the canvas: a column per step (pixels).
COLUMNS = (60, 330, 600, 900, 1170, 1470, 1740)
PORT_STEP = 33.4  # distance between the output ports of a Case
IN_OFFSET = (-9.2, 14.0)  # input port, from the widget's top-left corner
OUT_OFFSET = (165.2, 14.0)  # first output port


@dataclass(frozen=True)
class Route:
    """One Case branch: a routing value and the queue its work items go to."""

    value: str
    queue: str


def _id() -> str:
    """Return a new identifier."""
    return str(uuid.uuid4())


class _Flow:
    """Collects activities, links and their widgets while the flow is put together."""

    def __init__(self) -> None:
        self.activities: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.widgets: dict[str, dict[str, Any]] = {}
        self._ports: dict[str, dict[str, dict[str, Any]]] = {}
        self._by_name: dict[str, str] = {}

    def add(
        self,
        name: str,
        group: str,
        widget_type: str,
        properties: dict[str, Any],
        position: tuple[float, float],
        outputs: Sequence[str],
    ) -> None:
        """Add an activity and its widget; ``outputs`` are its output port names, top down."""
        aid = _id()
        props = {"name": name, "_renderRequestTimestamp": 0, **properties}
        self.activities[aid] = {"id": aid, "name": name, "group": group, "properties": props}
        self._by_name[name] = aid
        x, y = position
        ports: dict[str, dict[str, Any]] = {}
        if widget_type != "start":
            ports["in"] = self._port("in", True, x + IN_OFFSET[0], y + IN_OFFSET[1], "left")
        step = PORT_STEP if group == "enum-gateway" else 108.0
        top = 90.0 if widget_type == "start" else OUT_OFFSET[1]
        for i, port in enumerate(outputs):
            ports[port] = self._port(port, False, x + OUT_OFFSET[0], y + top + i * step, "right")
        self._ports[aid] = ports
        self.widgets[aid] = {
            "id": _id(),
            "type": widget_type,
            "widgetType": "activity",
            "label": "New Activity Widget in Diagram",
            "point": {"x": x, "y": y},
            "ports": list(ports.values()),
            "properties": props,
        }

    @staticmethod
    def _port(name: str, is_source: bool, x: float, y: float, side: str) -> dict[str, Any]:
        """Return a port as the canvas stores it (the input port is the 'source' one)."""
        return {
            "portId": _id(),
            "isSourcePort": is_source,
            "linkId": None,
            "type": "arrow",
            "links": [],
            "properties": {"x": x, "name": name, "y": y, "label": name, "alignment": side},
        }

    def link(self, source: str, port: str, target: str) -> None:
        """Connect output ``port`` of activity ``source`` to the input of ``target``."""
        sid, tid = self._by_name[source], self._by_name[target]
        lid, wid = _id(), _id()
        src, dst = self._ports[sid][port], self._ports[tid]["in"]
        self.links.append(
            {
                "id": lid,
                "sourceActivityId": sid,
                "targetActivityId": tid,
                "conditionExpr": port,
                "properties": {"value": port},
            }
        )
        for end in (src, dst):
            end["linkId"] = wid
            end["links"].append(wid)
        start = {"x": src["properties"]["x"] + 10, "y": src["properties"]["y"] + 10}
        stop = {"x": dst["properties"]["x"] + 10, "y": dst["properties"]["y"] + 10}
        colour = "var(--mds-color-theme-outline-theme-normal)"
        self.widgets[lid] = {
            "id": wid,
            "type": "arrow",
            "widgetType": "link",
            "label": "[object Object]",
            "properties": {
                "curvyness": 50,
                "selected": False,
                "color": colour,
                "points": [
                    {"id": _id(), "type": "point", **start},
                    {"id": _id(), "type": "point", **stop},
                ],
                "width": 2,
                "selectedColor": colour,
            },
            "points": [],
            "sourcePort": {
                "id": src["portId"],
                "activeWidgetId": self.widgets[sid]["id"],
                "point": {"x": round(start["x"]), "y": round(start["y"])},
            },
            "targetPort": {
                "id": dst["portId"],
                "activeWidgetId": self.widgets[tid]["id"],
                "point": {"x": round(stop["x"]), "y": round(stop["y"])},
            },
        }


def _case(expression: str, values: Sequence[str]) -> dict[str, Any]:
    """Return the properties of a Case with one numbered branch per value (ports ``0``, ``1``…)."""
    return {
        "activityId": "case-statement",
        "activityName": "case-statement",
        "activityVersionNumber": None,
        "expression": expression,
        "expression:radioName": "build-expression",
        "expression_radioName": "build-expression",
        "menuLinks": [str(i) for i in range(len(values))],
        "menuLinks_input": list(values),
        "menuLinks:input": list(values),
        "description": expression.strip("{}"),
    }


def _queue(queue: str, queue_ids: Mapping[str, str]) -> dict[str, Any]:
    """Return the properties of a Queue Contact on the Work Item queue ``queue``."""
    return {
        "activityId": "604b5eedf36fe105fe8dae31",
        "activityName": "queue-contact",
        "activityVersionNumber": 339,
        "activityType": "core",
        "channelType": "WORK_ITEM",
        "channelType:name": "Work Item",
        "channelType_name": "Work Item",
        "channelType:type": None,
        "channelType_type": None,
        "queueRadioGroup": "staticQueue",
        "destination": queue_ids.get(queue),
        "destination:name": queue,
        "destination_name": queue,
        "destination:type": None,
        "destination_type": None,
        "destinationVariable": None,
        "fallbackQueue": None,
        "fallbackQueue:name": None,
        "fallbackQueue_name": None,
        "skills": None,
        "toggle": False,
        "toggleAgentAvailability": False,
        "agentAvailabilityRadioGroup": None,
        "agentAvailabilityVariable": None,
        "priority": None,
        "priority:name": None,
        "priority_name": None,
        "priorityRadioGroup": None,
        "priorityVariable": None,
        "flowDecryptAccess": False,
    }


def _end() -> dict[str, Any]:
    """Return the properties of End Flow."""
    return {
        "activityId": "end",
        "activityName": "end",
        "activityVersionNumber": None,
        "activityType": "logic",
    }


def _event_flows() -> dict[str, Any]:
    """Return the global event handlers, empty, as Flow Designer creates them."""
    activities: dict[str, Any] = {}
    widgets: dict[str, Any] = {}
    on_events: dict[str, str] = {}
    for i, (name, display, spec, icon) in enumerate(EVENTS):
        aid = _id()
        props = {
            "eventSourceId": START_EVENT["eventSourceId"],
            "eventSourceName": "WebexContactCenter",
            "displayName": display,
            "iconDiagram": icon,
            "activityName": "Event",
            "activityId": "event",
            "eventSpecificationId": spec,
            "activityVersionNumber": None,
            "eventSpecificationName": name,
            "name": name,
            "eventClassificationId": START_EVENT["eventClassificationId"],
            "eventClassificationName": "VoiceInteractions",
            "event": display,
            "_renderRequestTimestamp": 0,
            "activityType": "logic",
        }
        activities[aid] = {"id": aid, "name": name, "group": "event", "properties": props}
        on_events[name] = aid
        y = 115 + 115 * i
        out = {"x": 265.2, "name": "out", "y": y + 14, "label": "out", "alignment": "right"}
        widgets[aid] = {
            "id": _id(),
            "type": "event",
            "widgetType": "activity",
            "label": "New Activity Widget in Diagram",
            "point": {"x": 100, "y": y},
            "ports": [
                {
                    "portId": _id(),
                    "isSourcePort": False,
                    "linkId": None,
                    "type": "arrow",
                    "links": [],
                    "properties": out,
                }
            ],
            "properties": props,
        }
    return {
        "eventsMap": {
            "GLOBAL_EVENTS": {
                "id": "GLOBAL_EVENTS",
                "name": "name",
                "description": "description",
                "process": {"activities": activities, "links": []},
                "onEvents": on_events,
                "diagram": {"widgets": widgets, "properties": {}},
            }
        },
        "properties": {"offsetX": 0, "offsetY": 0, "zoom": 0, "gridSize": 20},
    }


def sample_flow(
    name: str,
    routes: Sequence[Route],
    fallback_queue: str,
    queue_ids: Mapping[str, str] | None = None,
    channel: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Return the sample flow for ``routes``, with ``fallback_queue`` on the default branch.

    Args:
        name: Flow name in Flow Designer (unique in the organisation).
        routes: One Case branch per routing value, in order; values must be unique.
        fallback_queue: Queue of the default branch (a routing value without a branch).
        queue_ids: Id of each queue by name, from the organisation's configuration; a queue
            without an id is still shown by name and chosen again in Flow Designer.
        channel: Id and name of the Work Item channel; the flow must name it, or Flow Designer
            refuses its queues ("the channel type does not match what triggers the flow").

    Raises:
        ValueError: when a routing value occurs twice.
    """
    values = [route.value for route in routes]
    if len(set(values)) != len(values):
        raise ValueError("duplicate routing value")
    ids = queue_ids or {}
    flow = _Flow()
    n = len(routes)
    flow.add(
        "NewContact",
        "start",
        "start",
        {
            "activityId": "start",
            "activityName": "NewContact",
            "activityVersionNumber": None,
            "activityType": "logic",
            "event": "NewContact",
            "flowType": START_EVENT,
        },
        (COLUMNS[0], 40),
        ["out"],
    )
    flow.add(
        "CaseKind",
        "enum-gateway",
        "enum-gateway",
        _case(f"{{{{{WORK_ITEM_DATA}.kind}}}}", [PROBE_KIND]),
        (COLUMNS[1], 40),
        ["0", "error", "default"],
    )
    flow.add("EndProbe", "end", "end", _end(), (COLUMNS[2], 0), [])
    flow.add(
        "ScreenPopMeeting",
        "action",
        "action",
        {
            "activityId": "5f129c2178f95a6d46348a50",
            "activityName": "screen-pop",
            "activityVersionNumber": 60,
            "activityType": "core",
            "screenPopUrl": f"{{{{{WORK_ITEM_DATA}.meetingLink}}}}",
            "screenPopDesktopLabel": SCREEN_POP_LABEL,
            "target": "newBrowserTab",
            "queryParameters": {},
            "flowDecryptAccess": False,
        },
        (COLUMNS[3], 240),
        ["default"],
    )
    flow.add(
        "CaseGroup",
        "enum-gateway",
        "enum-gateway",
        _case(f"{{{{{ROUTING_KEY}}}}}", values),
        (COLUMNS[4], 40),
        [*(str(i) for i in range(n)), "error", "default"],
    )
    for i, route in enumerate(routes):
        flow.add(
            f"Queue_{i}",
            "action",
            "action",
            _queue(route.queue, ids),
            (COLUMNS[5], 40 + 140 * i),
            ["default", "failure"],
        )
    flow.add(
        "QueueDefault",
        "action",
        "action",
        _queue(fallback_queue, ids),
        (COLUMNS[5], 40 + 140 * n),
        ["default", "failure"],
    )
    flow.add("EndFlow", "end", "end", _end(), (COLUMNS[6], 40 + 70 * n), [])
    flow.link("NewContact", "out", "CaseKind")
    flow.link("CaseKind", "0", "EndProbe")
    flow.link("CaseKind", "default", "ScreenPopMeeting")
    flow.link("ScreenPopMeeting", "default", "CaseGroup")
    for i in range(n):
        flow.link("CaseGroup", str(i), f"Queue_{i}")
        flow.link(f"Queue_{i}", "default", "EndFlow")
    flow.link("CaseGroup", "default", "QueueDefault")
    flow.link("QueueDefault", "default", "EndFlow")
    return {
        "version": 1,
        "flowType": "FLOW",
        "name": re.sub(r"\W", "_", name),
        "description": "OpenPlanner work items: probe, Screen Pop, queue per group",
        "comment": "",
        "variables": [
            {
                "name": ROUTING_KEY,
                "description": "Routing value of the OpenPlanner group (flowSettings)",
                "type": "STRING",
                "value": "",
                "isCAD": False,
                "desktopLabel": "",
                "isAgentEditable": False,
                "source": "",
                "isReportable": True,
                "overwrite": False,
                "isSecure": False,
                "id": "",
                "isExternalized": False,
                "resourceType": "",
            }
        ],
        "process": {"activities": flow.activities, "links": flow.links},
        "diagram": {
            "widgets": flow.widgets,
            "properties": {"offsetX": 0, "offsetY": 100, "zoom": 70, "gridSize": 20},
        },
        "eventFlows": _event_flows(),
        "runtimeVariables": [],
        "validating": True,
        "persist": True,
        "validationResults": [],
        "variableOrders": {"pop-over": [], "interaction-panel": []},
        "settings": [
            {
                "name": "showActivityWait",
                "value": "false",
                "type": "BOOLEAN",
                "group": "activityWait",
            },
            {"name": "audioFile", "value": "", "type": "STRING", "group": "activityWait"},
            {"name": "delayTime", "value": "2000", "type": "INTEGER", "group": "activityWait"},
        ],
        "associatedChannels": (
            [{"channelType": "workItem", "id": channel[0], "name": channel[1]}] if channel else []
        ),
        "flowOutcome": None,
    }
