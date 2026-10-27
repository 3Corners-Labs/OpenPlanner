"""List, classify and remove OpenPlanner's webhook registrations at Webex and WxCC.

``webhooks-list`` and ``webhooks-reset`` (runbook, section 6) read every registration and give
each one a state:

- ``current``: points at ``WEBHOOK_BASE_URL``, for a resource and host account OpenPlanner uses.
- ``stale``: OpenPlanner's (target path ``/webhooks/webex`` or ``/webhooks/wxcc``, or named
  "OpenPlanner ...") but no longer right: another address (an old tunnel), no ``hostEmail``
  filter, a host account no group uses, an event OpenPlanner does not use, inactive, or a
  duplicate. Only stale registrations are ever deleted.
- ``foreign``: someone else's; shown with the target's origin only and never touched.

Webex is always listed with ``ownedBy=org``: without it Webex hides org-owned webhooks.
Of a registration only selected fields are kept: id, resource and event, filter, status,
whether a secret is set (never its value) and the target without query string; for foreign
registrations only the scheme and host, because other integrations may put tokens in their
URL. Ids from a listing are checked before they go into a DELETE path.

    GET /webhooks?ownedBy=org (+ next pages) ─┐
    GET /v2/subscriptions ────────────────────┴► classify ─► format / store ─► delete stale
"""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.adapters.webex_auth import AuthorizedClient
from app.adapters.webhook_registration import WEBEX_EVENTS, WXCC_EVENT_TYPES
from app.domain.ports import IntegrationError

MAX_PAGES = 10
API_ID = re.compile(r"[A-Za-z0-9_=-]{1,256}")
INACTIVE = frozenset({"inactive", "disabled", "suspended"})


class State(StrEnum):
    """Whether a registration is OpenPlanner's and still right."""

    CURRENT = "current"
    STALE = "stale"
    FOREIGN = "foreign"


class Reason(StrEnum):
    """Why a registration is stale or foreign (a code; the text is chosen where it is shown)."""

    NONE = ""
    FOREIGN = "foreign"
    OTHER_TARGET = "other_target"
    UNFILTERED = "unfiltered"
    UNKNOWN_HOST = "unknown_host"
    UNUSED_EVENT = "unused_event"
    INACTIVE = "inactive"
    DUPLICATE = "duplicate"


REASON_TEXT = {
    Reason.NONE: "",
    Reason.FOREIGN: "not OpenPlanner's: left alone",
    Reason.OTHER_TARGET: "points at another address (old tunnel or base URL), not WEBHOOK_BASE_URL",
    Reason.UNFILTERED: "no hostEmail filter: reports every meeting of the organisation",
    Reason.UNKNOWN_HOST: "host account that no group uses",
    Reason.UNUSED_EVENT: "event OpenPlanner does not use",
    Reason.INACTIVE: "inactive: the platform stopped delivering",
    Reason.DUPLICATE: "duplicate of another registration",
}
"""Operator text for the command line; the admin portal translates the codes itself."""


@dataclass(frozen=True)
class Expected:
    """What the current configuration registers: targets and the groups' host accounts."""

    webex_target: str
    wxcc_target: str | None
    hosts: tuple[str, ...]


@dataclass(frozen=True)
class Registration:
    """One webhook or subscription, reduced to fields that are safe to show."""

    platform: str
    id: str
    label: str
    target: str
    filter: str
    status: str
    secret: bool | None
    state: State
    reason: Reason


@dataclass(frozen=True)
class ListingError:
    """A listing that failed: platform and error code."""

    platform: str
    code: str


@dataclass(frozen=True)
class Listing:
    """Every registration found, what is missing and which listings failed."""

    registrations: list[Registration] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[ListingError] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether every listing succeeded."""
        return not self.errors

    @property
    def stale(self) -> list[Registration]:
        """The registrations ``webhooks-reset`` deletes."""
        return [r for r in self.registrations if r.state is State.STALE]


async def list_registrations(
    client: AuthorizedClient, webex_base_url: str, wxcc_base_url: str | None, expected: Expected
) -> Listing:
    """List and classify Webex webhooks and (with a WxCC host) WxCC subscriptions."""
    hosts = sorted({h.strip().lower() for h in expected.hosts if h.strip()})
    registrations: list[Registration] = []
    missing: list[str] = []
    errors: list[ListingError] = []
    try:
        items = await _webex_items(client, webex_base_url)
    except IntegrationError as error:
        errors.append(ListingError("webex", error.code))
    else:
        found = _classify_webex(items, expected.webex_target, hosts)
        registrations += found
        missing += _missing_webex(found, hosts)
    if wxcc_base_url:
        try:
            listed = await client.request("GET", f"{wxcc_base_url.rstrip('/')}/v2/subscriptions")
        except IntegrationError as error:
            errors.append(ListingError("wxcc", error.code))
        else:
            found = _classify_wxcc(_items(listed.json(), "data"), expected.wxcc_target)
            registrations += found
            if expected.wxcc_target and not any(r.state is State.CURRENT for r in found):
                missing.append("wxcc task events")
    return Listing(registrations, missing, errors)


async def _webex_items(client: AuthorizedClient, base_url: str) -> list[dict[str, Any]]:
    """Return every org-owned webhook, following ``Link: rel=next`` on the same https host."""
    base = base_url.rstrip("/")
    host = httpx.URL(base).host
    url: str | None = f"{base}/webhooks"
    params: dict[str, str] | None = {"max": "100", "ownedBy": "org"}
    items: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        if url is None:
            break
        response = await client.request("GET", url, params=params)
        items += _items(response.json(), "items")
        following = response.links.get("next", {}).get("url")
        next_url = httpx.URL(following) if following else None
        same_host = next_url and next_url.scheme == "https" and next_url.host == host
        url = str(next_url) if same_host else None
        params = None
    return items


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    """Return the list of objects under ``key``; anything else is ignored."""
    found = body.get(key) if isinstance(body, dict) else None
    return [i for i in found if isinstance(i, dict)] if isinstance(found, list) else []


def _classify_webex(
    items: Iterable[dict[str, Any]], target: str, hosts: list[str]
) -> list[Registration]:
    """Give each Webex webhook its state."""
    filters = {f"hostemail={h}" for h in hosts}
    events = {f"{r}/{e}" for r, e in WEBEX_EVENTS}
    seen: set[tuple[str, str]] = set()
    found = []
    for item in items:
        label = f"{item.get('resource')}/{item.get('event')}"
        url = str(item.get("targetUrl") or "")
        host_filter = str(item.get("filter") or "")
        status = str(item.get("status") or "")
        ours = _path(url).endswith("/webhooks/webex") or _named(item)
        if not ours:
            reason = Reason.FOREIGN
        elif url != target:
            reason = Reason.OTHER_TARGET
        elif not host_filter:
            reason = Reason.UNFILTERED
        elif host_filter.lower() not in filters:
            reason = Reason.UNKNOWN_HOST
        elif label not in events:
            reason = Reason.UNUSED_EVENT
        elif status.lower() in INACTIVE:
            reason = Reason.INACTIVE
        elif (label, host_filter.lower()) in seen:
            reason = Reason.DUPLICATE
        else:
            reason = Reason.NONE
            seen.add((label, host_filter.lower()))
        found.append(_registration("webex", item, label, url, host_filter, status, reason))
    return found


def _classify_wxcc(items: Iterable[dict[str, Any]], target: str | None) -> list[Registration]:
    """Give each WxCC subscription its state."""
    found = []
    current = False
    for item in items:
        url = str(item.get("destinationUrl") or "")
        types = item.get("eventTypes")
        event_types = [str(t) for t in types] if isinstance(types, list) else []
        status = str(item.get("status") or "")
        ours = _path(url).endswith("/webhooks/wxcc") or _named(item)
        if not ours:
            reason = Reason.FOREIGN
        elif url != target:
            reason = Reason.OTHER_TARGET
        elif not set(WXCC_EVENT_TYPES) <= set(event_types):
            reason = Reason.UNUSED_EVENT
        elif status.lower() in INACTIVE:
            reason = Reason.INACTIVE
        elif current:
            reason = Reason.DUPLICATE
        else:
            reason, current = Reason.NONE, True
        label = "task events" if ours else ",".join(event_types)[:60] or "subscription"
        found.append(_registration("wxcc", item, label, url, "", status, reason))
    return found


def _registration(
    platform: str,
    item: dict[str, Any],
    label: str,
    url: str,
    host_filter: str,
    status: str,
    reason: Reason,
) -> Registration:
    """Build a registration from the selected fields; the secret becomes a flag."""
    foreign = reason is Reason.FOREIGN
    if reason is Reason.NONE:
        state = State.CURRENT
    else:
        state = State.FOREIGN if foreign else State.STALE
    return Registration(
        platform=platform,
        id=str(item.get("id") or ""),
        label=label,
        target=_shown(url, origin_only=foreign),
        filter="" if foreign else host_filter,
        status=status or "-",
        secret=bool(item["secret"]) if "secret" in item else None,
        state=state,
        reason=reason,
    )


def _named(item: dict[str, Any]) -> bool:
    """Whether the registration carries OpenPlanner's name."""
    return str(item.get("name") or "").startswith("OpenPlanner")


def _path(url: str) -> str:
    """Return the path of ``url`` without a trailing slash."""
    return urlsplit(url).path.rstrip("/")


def _shown(url: str, *, origin_only: bool) -> str:
    """Return ``url`` without credentials, query and fragment (only the origin if asked)."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "-"
    origin = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return origin if origin_only else origin + parts.path


def _missing_webex(found: list[Registration], hosts: list[str]) -> list[str]:
    """Return the webhooks the groups' host accounts need but have no current registration."""
    have = {(r.label, r.filter.lower()) for r in found if r.state is State.CURRENT}
    return [
        f"webex {resource}/{event} [{host}]"
        for host in hosts
        for resource, event in WEBEX_EVENTS
        if (f"{resource}/{event}", f"hostemail={host}") not in have
    ]


async def delete_stale(
    client: AuthorizedClient,
    webex_base_url: str,
    wxcc_base_url: str | None,
    listing: Listing,
    *,
    confirmed: bool,
) -> list[str]:
    """Delete every stale registration (only with ``confirmed``); one report line each."""
    lines = []
    for item in listing.stale:
        name = f"{item.platform} {item.label}"
        if not API_ID.fullmatch(item.id):
            lines.append(f"skipped {name}: id is not a valid API id")
            continue
        if not confirmed:
            lines.append(f"would delete {name} {item.id} ({REASON_TEXT[item.reason]})")
            continue
        if item.platform == "webex":
            url = f"{webex_base_url.rstrip('/')}/webhooks/{item.id}"
        elif wxcc_base_url:
            url = f"{wxcc_base_url.rstrip('/')}/v2/subscriptions/{item.id}"
        else:  # pragma: no cover - WxCC entries are only listed with a WxCC host
            continue
        lines.append(await _delete(client, url, name, item))
    return lines


async def _delete(client: AuthorizedClient, url: str, name: str, item: Registration) -> str:
    """Delete one registration; return what happened."""
    try:
        await client.request("DELETE", url)
    except IntegrationError as error:
        if error.code == "rejected_404":
            return f"{name} {item.id}: already gone"
        if error.retryable:
            return f"{name} {item.id}: failed ({error.code}): try again later"
        hint = _refusal_hint(item.platform) if error.code == "rejected_403" else "see runbook"
        return f"{name} {item.id}: refused ({error.code}): {hint}"
    return f"deleted {name} {item.id}"


def _refusal_hint(platform: str) -> str:
    """Return what a refused delete most likely means."""
    if platform == "webex":
        return (
            "Webex quirk: a webhook Webex created during a refused create cannot be deleted "
            "by the Service App; see runbook, Webex quirk"
        )
    return "needs cjp:config_write"


def format_listing(listing: Listing) -> str:
    """Return the operator report: one line per registration, then missing and failures."""
    lines = []
    for r in listing.registrations:
        secret = {True: "secret set", False: "no secret", None: "secret unknown"}[r.secret]
        line = (
            f"{r.state.value.upper():<7}  {r.platform:<5}  {r.label:<28}  "
            f"{r.filter or 'no filter':<36}  {r.status:<8}  {secret:<14}  {r.target}  id={r.id}"
        )
        reason = REASON_TEXT[r.reason]
        lines.append(f"{line}\n         -> {reason}" if reason else line)
    lines += [f"MISSING  {m}" for m in listing.missing]
    lines += [f"FAILED   {e.platform} listing: refused ({e.code})" for e in listing.errors]
    counts = {s: sum(r.state is s for r in listing.registrations) for s in State}
    lines.append(
        f"{counts[State.CURRENT]} current, {counts[State.STALE]} stale, "
        f"{counts[State.FOREIGN]} foreign, {len(listing.missing)} missing"
    )
    if counts[State.STALE]:
        lines.append("Remove stale registrations: python -m app.cli webhooks-reset (dry run first)")
    if listing.missing:
        lines.append("Create missing registrations: python -m app.cli register-webhooks")
    return "".join(f"{line}\n" for line in lines)


def listing_to_report(listing: Listing) -> dict[str, Any]:
    """Return the listing as JSON-safe data for the admin portal (no secret values)."""
    return asdict(listing)


def listing_from_report(report: dict[str, Any]) -> Listing:
    """Rebuild a listing stored by ``listing_to_report``."""
    return Listing(
        registrations=[
            Registration(**(r | {"state": State(r["state"]), "reason": Reason(r["reason"])}))
            for r in report.get("registrations", [])
        ],
        missing=list(report.get("missing", [])),
        errors=[ListingError(**e) for e in report.get("errors", [])],
    )
