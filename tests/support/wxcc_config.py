"""A Webex Contact Center configuration as ``wxcc-config-check`` reads it, for its tests.

Shapes recorded from the product owner's organisation on 2026-10-25 (eu2): one Work Item
channel, an asset with its own schema, an entry point whose flow queues every contact to one
queue. Names are kept, ids replaced.
"""

from typing import Any

from app.adapters.wxcc_config import (
    Asset,
    Channel,
    EntryPoint,
    FlowSummary,
    Queue,
    WxccConfig,
    parse_flow,
)

OPENPLANNER_SCHEMA = {
    "bookingReference": (True, True),
    "firstName": (False, True),
    "startTime": (False, True),
    "groupName": (False, True),
    "meetingLink": (False, True),
    "kind": (False, True),
}
"""The asset's schema: each key OpenPlanner sends, as (required, viewable)."""
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
"""The entry point's flow as WxCC returns it: every contact goes to queue ``q-1``."""


def recorded_config(
    *,
    schema: dict[str, tuple[bool, bool]] | None = None,
    flow: FlowSummary | None = None,
    queues: tuple[Queue, ...] = (Queue("q-1", "videoafspraak_prototype", "WORK_ITEM"),),
) -> WxccConfig:
    """Return the recorded configuration, with the schema, the flow or the queues replaced."""
    return WxccConfig(
        channels=(Channel("ch-1", "videoafspraak-prototype", "WORK_ITEM"),),
        assets=(
            Asset(
                "as-1",
                "videoafspraak-prototype",
                "ch-1",
                "videoafspraak@example.video",
                OPENPLANNER_SCHEMA if schema is None else schema,
            ),
        ),
        entry_points=(EntryPoint("ep-1", "videoafspraak_prototype", "as-1", "fl-1", True),),
        queues=queues,
        flows={"fl-1": flow if flow is not None else parse_flow(FLOW_DOC)},
    )
