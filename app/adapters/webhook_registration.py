"""Register OpenPlanner's webhooks at Webex and WxCC (docs/webhooks.md), idempotently.

Webex: org-owned webhooks per resource, event and **host account** (``hostEmail`` filter, one
set per distinct group host), signed with the webhook secret. Without the filter an org-owned
webhook reports every meeting of the organisation; with it only OpenPlanner's host accounts.
Unfiltered webhooks on the same target are reported as stale; ``webhooks-reset`` removes them.
WxCC: one v2 subscription for the task events, signed with the subscription secret. Existing
registrations for the same target URL are left alone, so running it twice changes nothing.
A refused registration is reported with the scope it most likely needs and the others are
still tried; the report never contains the secret.

A rotated secret (ADR 0124 point 8) reaches the platform without a gap in the events, and each
way of applying it is one function with one table:

- the video platform's webhooks are updated in place (``update_webex_secrets``);
- a WxCC subscription keeps its secret, so ``renew_wxcc`` makes the new subscription before it
  removes the old one. ``SECRET_UPDATES`` maps what an adapter declares it can do
  (``ContactCenterCapabilities.webhook_secret_update``) to the function that does it; the
  organisations' dispatcher and the command line both go through ``secret_update``.

Both rely on the previous secret still verifying for the overlap, which is what makes either order
safe.
"""

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from app.adapters.webex_auth import AuthorizedClient
from app.domain.contact_center import Registration, WebhookSecretUpdate
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

SUBSCRIPTION_ID = re.compile(r"[A-Za-z0-9_=-]{1,256}")
"""An id from a listing, checked before it goes into a PUT or DELETE path."""


@dataclass(frozen=True)
class _Hook:
    """One Webex webhook to make or update: where, what and for which host account."""

    client: AuthorizedClient
    base: str
    target_url: str
    secret: str
    resource: str
    event: str
    host: str

    @property
    def label(self) -> str:
        """The report line's prefix: ``resource/event [host]``."""
        return f"{self.resource}/{self.event} [{self.host}]"

    @property
    def host_filter(self) -> str:
        """The ``hostEmail`` filter that limits the webhook to the group's host account."""
        return f"hostEmail={self.host}"


_OnExisting = Callable[[_Hook, dict[str, object]], Awaitable[str]]
"""What is done for a webhook that exists: its report line."""


async def _webex_report(
    client: AuthorizedClient,
    base_url: str,
    target_url: str,
    secret: str,
    hosts: Iterable[str],
    on_existing: _OnExisting,
) -> list[str]:
    """Make the missing webhooks per host account, and do ``on_existing`` for the others."""
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
    held = set(existing)
    for host in accounts:
        for resource, event in WEBEX_EVENTS:
            hook = _Hook(client, base, target_url, secret, resource, event, host)
            found = existing.get((resource, event, hook.host_filter))
            if found is not None:
                report.append(await on_existing(hook, found))
            else:
                report.append(await _register_one(hook, held))
    report.extend(
        f"{i.get('resource')}/{i.get('event')} [all meetings]: stale (no hostEmail filter, "
        f"every meeting of the organisation): webhook {i.get('id')}, remove with webhooks-reset"
        for i in ours
        if not i.get("filter")
    )
    return report


async def _leave(hook: _Hook, found: dict[str, object]) -> str:
    """Leave an existing webhook as it is."""
    return f"{hook.label}: exists"


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
    return await _webex_report(client, base_url, target_url, secret, hosts, _leave)


async def update_webex_secrets(
    client: AuthorizedClient,
    base_url: str,
    target_url: str,
    secret: str,
    *,
    hosts: Iterable[str],
) -> list[str]:
    """Give the existing webhooks per host account ``secret``, in place; create the missing ones.

    The video platform changes the secret of a webhook without making it anew, so no event is
    lost (ADR 0124 point 8). Lines read ``resource/event [host]: updated|created|refused (...)``.
    """
    return await _webex_report(client, base_url, target_url, secret, hosts, _update_one)


async def _register_one(hook: _Hook, existing: set[tuple[object, object, str]]) -> str:
    """Create one filtered webhook unless it exists; return its report line."""
    if (hook.resource, hook.event, hook.host_filter) in existing:
        return f"{hook.label}: exists"
    try:
        await hook.client.request(
            "POST",
            f"{hook.base}/webhooks",
            json={
                "name": f"OpenPlanner {hook.resource} {hook.event}",
                "targetUrl": hook.target_url,
                "resource": hook.resource,
                "event": hook.event,
                "filter": hook.host_filter,
                "secret": hook.secret,
                "ownedBy": "org",
            },
        )
    except IntegrationError as error:
        if error.retryable:
            raise
        if error.code == "rejected_409":  # Webex: the same webhook exists already
            return f"{hook.label}: exists"
        return f"{hook.label}: refused ({error.code}): {_webex_hint(hook.resource, error)}"
    return f"{hook.label}: created"


async def _update_one(hook: _Hook, found: dict[str, object]) -> str:
    """Give one existing webhook the new secret, in place; return its report line."""
    webhook_id = str(found.get("id") or "")
    if not SUBSCRIPTION_ID.fullmatch(webhook_id):
        return f"{hook.label}: refused (no usable id): left as it was"
    try:
        await hook.client.request(
            "PUT",
            f"{hook.base}/webhooks/{webhook_id}",
            json={
                "name": f"OpenPlanner {hook.resource} {hook.event}",
                "targetUrl": hook.target_url,
                "secret": hook.secret,
                "ownedBy": "org",
                "status": "active",
            },
        )
    except IntegrationError as error:
        if error.retryable:
            raise
        return f"{hook.label}: refused ({error.code}): {_webex_hint(hook.resource, error)}"
    return f"{hook.label}: updated"


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
    refusal = await _create_wxcc(client, base, target_url, secret)
    return "wxcc subscription: created" if refusal is None else _refused_line(refusal)


def _refused_line(error: IntegrationError) -> str:
    """Return the report line for a refused WxCC create: the code and what it most likely needs."""
    reason = (
        "needs cjp:config_write"
        if error.code in ("unauthorized", "rejected_403")
        else "WxCC refused the request (destination must be https on port 443 with a public "
        "domain; secret 32+ characters)"
    )
    return f"wxcc subscription: refused ({error.code}): {reason}"


async def _create_wxcc(
    client: AuthorizedClient, base: str, target_url: str, secret: str
) -> IntegrationError | None:
    """Create the WxCC subscription at ``target_url``; ``None``, or the refusal.

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
        return error
    return None


NOT_A_DUPLICATE = frozenset(
    {"unauthorized", "rejected_400", "rejected_401", "rejected_403", "rejected_422"}
)
"""Refusals of a create that cannot mean "a second subscription at this address": missing rights
or a request WxCC finds invalid. The shape of WxCC's answer for a duplicate is not recorded, so
a duplicate is any other refusal while the old subscription still holds the address."""


async def _held_ids(client: AuthorizedClient, base: str, target_url: str) -> list[str] | None:
    """Return the ids of the subscriptions at ``target_url`` (clean ids only), or ``None``.

    ``None`` means the listing is not the expected object.

    Raises:
        IntegrationError: Retryable failures.
    """
    try:
        items = (await client.request("GET", f"{base}/v2/subscriptions")).json()["data"]
        return [
            str(item["id"])
            for item in items
            if item.get("destinationUrl") == target_url
            and SUBSCRIPTION_ID.fullmatch(str(item.get("id") or ""))
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def _delete_subscriptions(client: AuthorizedClient, base: str, ids: list[str]) -> None:
    """Delete the subscriptions ``ids``."""
    for item_id in ids:
        await client.request("DELETE", f"{base}/v2/subscriptions/{item_id}")


async def renew_wxcc(
    client: AuthorizedClient, base_url: str, target_url: str, secret: str
) -> Registration:
    """Make the one WxCC subscription at ``target_url`` carry ``secret`` (ADR 0032 CC-4d, 0124 8).

    WxCC keeps a subscription's secret, so a new secret needs a new subscription. The new one is
    made *before* the old ones are removed, so the platform never has none: until the old ones are
    gone an event is delivered twice, once signed with each secret, and the replay memory
    recognises the second (its key does not depend on the secret). Only subscriptions at
    ``target_url`` are removed, their ids checked.

    A refusal of the first create leaves the old subscription alone, unless it can mean that WxCC
    allows one subscription per address (the answer for that is not recorded): the address is
    listed again, and only when the old subscription still holds it and the refusal is not one of
    rights or validation (``NOT_A_DUPLICATE``) are the old ones removed and the new one made
    then. That fall-back opens a gap in which events are lost, so its outcome says so.

    Returns:
        ``REGISTERED`` (made, old removed); ``REPLACED`` (the fall-back worked, events in the gap
        may have been missed); ``GONE`` (the fall-back removed the old subscription and the new
        one was refused: there is none now); ``REFUSED`` (nothing changed, or an odd listing).

    Raises:
        IntegrationError: Retryable failures (``FAILED`` for the caller); nothing is removed after
            one, so the old subscription keeps working under the previous secret.
    """
    base = base_url.rstrip("/")
    ours = await _held_ids(client, base, target_url)
    if ours is None:
        return Registration.REFUSED
    refusal = await _create_wxcc(client, base, target_url, secret)
    if refusal is None:
        await _delete_subscriptions(client, base, ours)
        return Registration.REGISTERED
    if not ours or refusal.code in NOT_A_DUPLICATE:
        return Registration.REFUSED
    still_held = await _held_ids(client, base, target_url)
    if not still_held:
        return Registration.REFUSED
    await _delete_subscriptions(client, base, still_held)
    if await _create_wxcc(client, base, target_url, secret) is None:
        return Registration.REPLACED
    return Registration.GONE


SecretUpdate = Callable[[AuthorizedClient, str, str, str], Awaitable[Registration]]
"""Applies a new secret to an organisation's subscription: client, API host, address, secret."""

SECRET_UPDATES: dict[WebhookSecretUpdate, SecretUpdate] = {WebhookSecretUpdate.RENEW: renew_wxcc}
"""The function that applies a new secret for each way a contact-center platform may allow it."""


def secret_update(capability: WebhookSecretUpdate) -> SecretUpdate:
    """Return the function that applies a new secret as ``capability`` says (one table)."""
    return SECRET_UPDATES[capability]


def renewal_line(outcome: Registration) -> str:
    """Return the command line's report line for a WxCC renewal; it never holds a secret."""
    return {
        Registration.REGISTERED: "wxcc subscription: renewed",
        Registration.REPLACED: (
            "wxcc subscription: renewed by replacing the old one; events in between may have "
            "been missed"
        ),
        Registration.GONE: (
            "wxcc subscription: refused after the old one was removed; there is none now, run "
            "register-webhooks again"
        ),
    }.get(outcome, "wxcc subscription: refused (see the request log)")
