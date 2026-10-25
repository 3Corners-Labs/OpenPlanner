"""Register OpenPlanner's webhooks at Webex and WxCC (docs/webhooks.md), idempotently.

Webex: one org-owned webhook per resource and event, signed with the webhook secret. WxCC: one
v2 subscription for the task events, signed with the subscription secret. Existing
registrations for the same target URL are left alone, so running it twice changes nothing.
"""

from app.adapters.webex_auth import AuthorizedClient

WEBEX_EVENTS = (
    ("meetings", "started"),
    ("meetings", "ended"),
    ("meetingParticipants", "joined"),
    ("meetingParticipants", "left"),
)
WXCC_EVENT_TYPES = ["task:new", "task:connected", "task:ended", "task:failed"]


async def register_webex(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> list[str]:
    """Create the missing Webex webhooks; return ``resource/event: created|exists`` per event."""
    base = base_url.rstrip("/")
    listed = (await client.request("GET", f"{base}/webhooks", params={"max": "100"})).json()
    existing = {
        (item.get("resource"), item.get("event"))
        for item in listed.get("items", [])
        if item.get("targetUrl") == target_url
    }
    report = []
    for resource, event in WEBEX_EVENTS:
        if (resource, event) in existing:
            report.append(f"{resource}/{event}: exists")
            continue
        await client.request(
            "POST",
            f"{base}/webhooks",
            json={
                "name": f"OpenPlanner {resource} {event}",
                "targetUrl": target_url,
                "resource": resource,
                "event": event,
                "secret": secret,
                "ownedBy": "org",
            },
        )
        report.append(f"{resource}/{event}: created")
    return report


async def register_wxcc(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> str:
    """Create the WxCC subscription if none points at ``target_url``; return ``created|exists``."""
    base = base_url.rstrip("/")
    listed = (await client.request("GET", f"{base}/v2/subscriptions")).json()
    if any(item.get("destinationUrl") == target_url for item in listed.get("data", [])):
        return "wxcc subscription: exists"
    await client.request(
        "POST",
        f"{base}/v2/subscriptions",
        json={
            "name": "OpenPlanner task events",
            "description": "Work item lifecycle for OpenPlanner bookings",
            "eventTypes": WXCC_EVENT_TYPES,
            "destinationUrl": target_url,
            "secret": secret,
        },
    )
    return "wxcc subscription: created"
