"""Register OpenPlanner's webhooks at Webex and WxCC (docs/webhooks.md), idempotently.

Webex: one org-owned webhook per resource and event, signed with the webhook secret. WxCC: one
v2 subscription for the task events, signed with the subscription secret. Existing
registrations for the same target URL are left alone, so running it twice changes nothing.
A refused registration is reported with the scope it most likely needs and the others are
still tried; the report never contains the secret.
"""

from app.adapters.webex_auth import AuthorizedClient
from app.domain.ports import IntegrationError

WEBEX_EVENTS = (
    ("meetings", "started"),
    ("meetings", "ended"),
    ("meetingParticipants", "joined"),
    ("meetingParticipants", "left"),
)
WXCC_EVENT_TYPES = ["task:new", "task:connected", "task:ended", "task:failed"]
WXCC_RESOURCE_VERSION = "task:1.0.0"
"""Subscriptions v2 require one ``resource:version``; see ``GET /v2/event-types``."""

WEBEX_SCOPES = {
    "meetings": "meeting:admin_schedule_read",
    "meetingParticipants": "meeting:admin_participants_read",
}
"""Admin read scope per resource that an org-owned webhook needs (Webex webhooks guide)."""
ORG_WEBHOOK_ROLE = "authorised by a Webex site admin or Compliance Officer"


async def register_webex(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> list[str]:
    """Create the missing Webex webhooks; return ``resource/event: created|exists`` per event."""
    base = base_url.rstrip("/")
    report = []
    try:
        listed = (
            await client.request("GET", f"{base}/webhooks", params={"max": "100", "ownedBy": "org"})
        ).json()
    except IntegrationError as error:
        if error.retryable:
            raise
        # Without the listing every webhook is created; Webex answers 409 for existing ones.
        report.append(f"webhook listing: refused ({error.code}): {_webex_hint('meetings', error)}")
        listed = {}
    existing = {
        (item.get("resource"), item.get("event"))
        for item in listed.get("items", [])
        if item.get("targetUrl") == target_url
    }
    for resource, event in WEBEX_EVENTS:
        if (resource, event) in existing:
            report.append(f"{resource}/{event}: exists")
            continue
        try:
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
        except IntegrationError as error:
            if error.retryable:
                raise
            if error.code == "rejected_409":  # Webex: the same webhook exists already
                report.append(f"{resource}/{event}: exists")
                continue
            report.append(
                f"{resource}/{event}: refused ({error.code}): {_webex_hint(resource, error)}"
            )
            continue
        report.append(f"{resource}/{event}: created")
    return report


def _webex_hint(resource: str, error: IntegrationError) -> str:
    """Return what a refused org-owned webhook call most likely needs."""
    if error.code in ("unauthorized", "rejected_403"):
        return f"needs {WEBEX_SCOPES[resource]}, {ORG_WEBHOOK_ROLE}"
    return "Webex refused the request (target must be a public https URL)"


async def register_wxcc(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> str:
    """Create the WxCC subscription if none points at ``target_url``; return ``created|exists``."""
    base = base_url.rstrip("/")
    listed = (await client.request("GET", f"{base}/v2/subscriptions")).json()
    if any(item.get("destinationUrl") == target_url for item in listed.get("data", [])):
        return "wxcc subscription: exists"
    try:
        await client.request(
            "POST",
            f"{base}/v2/subscriptions",
            json={
                "name": "OpenPlanner task events",
                "description": "Work item lifecycle for OpenPlanner bookings",
                "eventTypes": WXCC_EVENT_TYPES,
                "resourceVersion": WXCC_RESOURCE_VERSION,
                "destinationUrl": target_url,
                "secret": secret,
            },
        )
    except IntegrationError as error:
        if error.retryable:
            raise
        reason = (
            "needs cjp:config_write"
            if error.code in ("unauthorized", "rejected_403")
            else "WxCC refused the request (destination must be https on port 443 with a public "
            "domain; secret 32+ characters)"
        )
        return f"wxcc subscription: refused ({error.code}): {reason}"
    return "wxcc subscription: created"
