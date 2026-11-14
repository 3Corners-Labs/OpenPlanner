"""Register OpenPlanner's webhooks at Webex and WxCC (docs/webhooks.md), idempotently.

Webex: org-owned webhooks per resource, event and **host account** (``hostEmail`` filter, one
set per distinct group host), signed with the webhook secret. Without the filter an org-owned
webhook reports every meeting of the organisation; with it only OpenPlanner's host accounts.
Unfiltered webhooks on the same target are reported as stale; ``webhooks-reset`` removes them.
WxCC: one v2 subscription for the task events, signed with the subscription secret. Existing
registrations for the same target URL are left alone, so running it twice changes nothing.
A refused registration is reported with the scope it most likely needs and the others are
still tried; the report never contains the secret.

A rotated secret (ADR 0124 point 8) reaches the platform without a gap in the events: Webex
webhooks are updated in place with the new secret (``update_secret``); a WxCC subscription keeps
its secret, so ``renew_wxcc`` makes the new subscription before it removes the old one. Both rely
on the previous secret still verifying for the overlap, which is what makes either order safe.
"""

import re
from collections.abc import Iterable

from app.adapters.webex_auth import AuthorizedClient
from app.domain.contact_center import Registration
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
    update_secret: bool = False,
) -> list[str]:
    """Create the missing webhooks per host account; one report line per webhook.

    Lines read ``resource/event [host]: created|exists|updated|refused (...)``, plus one
    ``stale`` line per unfiltered webhook on ``target_url``. With ``update_secret`` an existing
    webhook is updated in place with ``secret`` instead of left alone (ADR 0124 point 8: the
    video platform changes the secret of a webhook without making it anew, so no event is lost).
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
    existing = {(i.get("resource"), i.get("event"), i.get("filter") or ""): i for i in ours}
    for host in accounts:
        for resource, event in WEBEX_EVENTS:
            found = existing.get((resource, event, f"hostEmail={host}"))
            if update_secret and found is not None:
                report.append(
                    await _update_one(
                        client, base, target_url, secret, resource, event, host, found
                    )
                )
                continue
            report.append(
                await _register_one(
                    client, base, target_url, secret, resource, event, host, set(existing)
                )
            )
    report.extend(
        f"{i.get('resource')}/{i.get('event')} [all meetings]: stale (no hostEmail filter, "
        f"every meeting of the organisation): webhook {i.get('id')}, remove with webhooks-reset"
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


async def _update_one(
    client: AuthorizedClient,
    base: str,
    target_url: str,
    secret: str,
    resource: str,
    event: str,
    host: str,
    found: dict[str, object],
) -> str:
    """Give one existing webhook the new secret, in place; return its report line."""
    label = f"{resource}/{event} [{host}]"
    webhook_id = str(found.get("id") or "")
    if not SUBSCRIPTION_ID.fullmatch(webhook_id):
        return f"{label}: refused (no usable id): left as it was"
    try:
        await client.request(
            "PUT",
            f"{base}/webhooks/{webhook_id}",
            json={
                "name": f"OpenPlanner {resource} {event}",
                "targetUrl": target_url,
                "secret": secret,
                "ownedBy": "org",
                "status": "active",
            },
        )
    except IntegrationError as error:
        if error.retryable:
            raise
        return f"{label}: refused ({error.code}): {_webex_hint(resource, error)}"
    return f"{label}: updated"


def _webex_hint(resource: str, error: IntegrationError) -> str:
    """Return what a refused org-owned webhook call most likely needs."""
    if error.code in ("unauthorized", "rejected_403"):
        return f"needs {WEBEX_SCOPES[resource]}, {ORG_WEBHOOK_ROLE}"
    return "Webex refused the request (target must be a public https URL)"


async def register_wxcc(
    client: AuthorizedClient,
    base_url: str,
    target_url: str,
    secret: str,
    *,
    update_secret: bool = False,
) -> str:
    """Create the WxCC subscription if none points at ``target_url``; return ``created|exists``.

    With ``update_secret`` the subscription is renewed with ``secret`` (``renew_wxcc``) and the
    line says ``renewed`` or why it was refused (ADR 0124 point 8).
    """
    base = base_url.rstrip("/")
    if update_secret:
        outcome = await renew_wxcc(client, base, target_url, secret)
        return (
            "wxcc subscription: renewed"
            if outcome is Registration.REGISTERED
            else "wxcc subscription: refused (see the request log)"
        )
    listed = (await client.request("GET", f"{base}/v2/subscriptions")).json()
    if any(item.get("destinationUrl") == target_url for item in listed.get("data", [])):
        return "wxcc subscription: exists"
    refusal = await _create_wxcc(client, base, target_url, secret)
    return "wxcc subscription: created" if refusal is None else f"wxcc subscription: {refusal}"


async def _create_wxcc(
    client: AuthorizedClient, base: str, target_url: str, secret: str
) -> str | None:
    """Create the WxCC subscription at ``target_url``; ``None``, or why WxCC refused it.

    Raises:
        IntegrationError: Retryable failures.
    """
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
        return f"refused ({error.code}): {reason}"
    return None


SUBSCRIPTION_ID = re.compile(r"[A-Za-z0-9_=-]{1,256}")
"""An id from the subscription listing, checked before it goes into a DELETE path."""


async def renew_wxcc(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> Registration:
    """Make the one WxCC subscription at ``target_url`` carry ``secret`` (ADR 0032 CC-4d, 0124 8).

    WxCC keeps a subscription's secret, so a new secret needs a new subscription. The new one is
    made *before* the old ones are removed, so the platform never has none: until the old ones are
    gone an event is delivered twice, once signed with each secret, and the replay memory
    recognises the second (its key does not depend on the secret). Only subscriptions at
    ``target_url`` are removed, their ids checked. If WxCC refuses a second subscription at the
    same address, the old ones are removed first and the new one made then, as before: events in
    that short moment rely on the platform's own retry, and the previous secret does not help.
    A listing that is not the expected object counts as refused; nothing is removed then.

    Raises:
        IntegrationError: Retryable failures (``FAILED`` for the caller); nothing is removed after
            one, so the old subscription keeps working under the previous secret.
    """
    base = base_url.rstrip("/")
    try:
        items = (await client.request("GET", f"{base}/v2/subscriptions")).json()["data"]
        ours = [
            str(item["id"])
            for item in items
            if item.get("destinationUrl") == target_url
            and SUBSCRIPTION_ID.fullmatch(str(item.get("id") or ""))
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return Registration.REFUSED
    refusal = await _create_wxcc(client, base, target_url, secret)
    if refusal is not None and ours:
        for item_id in ours:
            await client.request("DELETE", f"{base}/v2/subscriptions/{item_id}")
        ours = []
        refusal = await _create_wxcc(client, base, target_url, secret)
    for item_id in ours:
        await client.request("DELETE", f"{base}/v2/subscriptions/{item_id}")
    return Registration.REGISTERED if refusal is None else Registration.REFUSED
