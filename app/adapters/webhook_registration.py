"""Register OpenPlanner's webhooks at Webex and WxCC (docs/webhooks.md), idempotently.

Webex: org-owned webhooks per resource, event and **host account** (``hostEmail`` filter, one
set per distinct group host), signed with the webhook secret. Without the filter an org-owned
webhook reports every meeting of the organisation; with it only OpenPlanner's host accounts.
Unfiltered webhooks on the same target are reported as stale, never deleted automatically.
WxCC: one v2 subscription for the task events, signed with the subscription secret. Existing
registrations for the same target URL are left alone, so running it twice changes nothing.
A refused registration is reported with the scope it most likely needs and the others are
still tried; the report never contains the secret.
"""

from collections.abc import Iterable

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
    client: AuthorizedClient,
    base_url: str,
    target_url: str,
    secret: str,
    *,
    hosts: Iterable[str],
) -> list[str]:
    """Create the missing webhooks per host account; one report line per webhook.

    Lines read ``resource/event [host]: created|exists|refused (...)``, plus one ``stale``
    line per unfiltered webhook on ``target_url``.
    """
    base = base_url.rstrip("/")
    accounts = sorted({h.strip().lower() for h in hosts if h.strip()})
    report = []
    if not accounts:
        report.append("webex: no group has a video host account; no webhooks registered")
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
    ours = [item for item in listed.get("items", []) if item.get("targetUrl") == target_url]
    existing = {(i.get("resource"), i.get("event"), i.get("filter") or "") for i in ours}
    for host in accounts:
        for resource, event in WEBEX_EVENTS:
            report.append(
                await _register_one(
                    client, base, target_url, secret, resource, event, host, existing
                )
            )
    report.extend(
        f"{i.get('resource')}/{i.get('event')} [all meetings]: stale (no hostEmail filter, "
        f"every meeting of the organisation): delete webhook {i.get('id')}"
        for i in ours
        if not i.get("filter")
    )
    return report


async def _register_one(
    client: AuthorizedClient,
    base: str,
    target_url: str,
    secret: str,
    resource: str,
    event: str,
    host: str,
    existing: set[tuple[object, object, str]],
) -> str:
    """Create one filtered webhook unless it exists; return its report line."""
    label = f"{resource}/{event} [{host}]"
    host_filter = f"hostEmail={host}"
    if (resource, event, host_filter) in existing:
        return f"{label}: exists"
    try:
        await client.request(
            "POST",
            f"{base}/webhooks",
            json={
                "name": f"OpenPlanner {resource} {event}",
                "targetUrl": target_url,
                "resource": resource,
                "event": event,
                "filter": host_filter,
                "secret": secret,
                "ownedBy": "org",
            },
        )
    except IntegrationError as error:
        if error.retryable:
            raise
        if error.code == "rejected_409":  # Webex: the same webhook exists already
            return f"{label}: exists"
        return f"{label}: refused ({error.code}): {_webex_hint(resource, error)}"
    return f"{label}: created"


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
