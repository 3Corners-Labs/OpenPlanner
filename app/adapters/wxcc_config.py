"""``wxcc-config-check``: does WxCC's configuration route each group's work items? (read-only).

A work item reaches a queue through a chain configured in Control Hub (runbook, "Matching a
group to its queue"):

    group channel ─► Work Item channel        group business address ─► asset (on that channel)
    asset ─► entry point ─► flow ─► openplannerGroup ─► Case branch ─► Queue Contact ─► queue

This module reads that chain through the WxCC configuration APIs (``cjp:config_read``) and
compares it with every OpenPlanner group that has WxCC fields. It keeps only names, ids,
business addresses and schema keys: asset webhook secrets and queue agent lists are never
requested or kept. Flows are exported and reduced to their variable names, activity names and
string values, to find the routing variable, its branches and the queues used.

The queue naming rule is advice, not a requirement (``WARN``): the queue a group lands in
should contain the group's routing value as a name part, with ``-``, ``_``, spaces and case
treated alike (``aangifte`` matches ``FIN-BLD_videoafspraak_aangifte_01``).
"""

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.adapters.diagnostics import CheckResult
from app.adapters.wxcc import PROBE_KIND, REQUIRED_FIELDS, ROUTING_KEY, WORK_ITEM_FIELDS
from app.domain.ports import IntegrationError

PROJECT_ID = "5e5c9ad6d61f870d6d778c1b"
"""Flow Designer's project id: documented as the same for every organisation."""

REGIONS = ("eu1", "eu2", "us1", "ca1", "anz1", "jp1", "sg1")
WORK_ITEM = "WORK_ITEM"
PAGE_SIZE = 100
MAX_PAGES = 20

Result = Callable[..., CheckResult]
"""Builds one group's check line: ``result(name, ok, hint="ok", *, required=True)``."""


@dataclass(frozen=True)
class Channel:
    """A WxCC channel (only Work Item channels matter here)."""

    id: str
    name: str
    type: str


@dataclass(frozen=True)
class Asset:
    """A Work Item asset: its channel, business address and schema (key → required, viewable)."""

    id: str
    name: str
    channel_id: str
    business_address: str
    fields: dict[str, tuple[bool, bool]]


@dataclass(frozen=True)
class EntryPoint:
    """A Work Item entry point and the flow behind it."""

    id: str
    name: str
    asset_id: str | None
    flow_id: str | None
    active: bool


@dataclass(frozen=True)
class Queue:
    """A queue: name and channel type only (never its agents)."""

    id: str
    name: str
    channel_type: str


@dataclass(frozen=True)
class FlowSummary:
    """What a flow export reveals about routing: variables, activities and string values."""

    variables: frozenset[str]
    activities: frozenset[str]
    strings: frozenset[str] = field(repr=False)


@dataclass(frozen=True)
class WxccConfig:
    """The part of the organisation's WxCC configuration that routes work items."""

    channels: tuple[Channel, ...]
    assets: tuple[Asset, ...]
    entry_points: tuple[EntryPoint, ...]
    queues: tuple[Queue, ...]
    flows: dict[str, FlowSummary | None]


@dataclass(frozen=True)
class GroupRouting:
    """An OpenPlanner group's three WxCC values."""

    slug: str
    channel: str
    business_address: str
    routing_value: str


def names_match(value: str, name: str) -> bool:
    """Return whether ``value`` appears in ``name`` as whole name parts (``-``/``_``/case alike)."""
    wanted, parts = _parts(value), _parts(name)
    return bool(wanted) and any(
        parts[i : i + len(wanted)] == wanted for i in range(len(parts) - len(wanted) + 1)
    )


def _parts(text: str) -> list[str]:
    """Split a name into lower-case parts on ``-``, ``_`` and spaces."""
    return [part for part in re.split(r"[-_\s]+", text.lower()) if part]


def parse_flow(doc: dict[str, Any]) -> FlowSummary:
    """Reduce a flow export to variable names, activity names and all string values."""
    variables = {
        str(v["name"]) for v in doc.get("variables") or [] if isinstance(v, dict) and "name" in v
    }
    activities = {
        str(node.get("properties", {}).get("activityName"))
        for node in doc.get("nodes") or []
        if isinstance(node, dict) and node.get("properties", {}).get("activityName")
    }
    return FlowSummary(frozenset(variables), frozenset(activities), frozenset(_strings(doc)))


def _strings(value: object) -> Iterator[str]:
    """Yield every string inside ``value``."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def check_groups(groups: Iterable[GroupRouting], config: WxccConfig) -> list[CheckResult]:
    """Return the checks for every group, in the order of the routing chain."""
    groups = list(groups)
    results: list[CheckResult] = []
    for group in groups:
        sharing = sorted(g.slug for g in groups if g.business_address == group.business_address)
        results.extend(check_group(group, config, sharing))
    return results


def check_group(group: GroupRouting, config: WxccConfig, sharing: list[str]) -> list[CheckResult]:
    """Walk one group's chain; stop at the first link that is missing."""

    def result(
        name: str,
        ok: bool,
        hint: str = "ok",
        *,
        required: bool = True,
        code: str = "",
        **params: str,
    ) -> CheckResult:
        return CheckResult(
            f"[{group.slug}] {name}", None, ok, hint, required, code, tuple(params.items())
        )

    channel = next((c for c in config.channels if c.name == group.channel), None)
    work_item_channels = [c.name for c in config.channels if c.type == WORK_ITEM]
    if channel is None:
        out = [
            result(
                "channel",
                False,
                f"no channel named '{group.channel}'; Work Item "
                f"channels: {', '.join(work_item_channels) or 'none'}",
                code="channel.missing",
                channel=str(group.channel),
                available=", ".join(work_item_channels) or "-",
            )
        ]
    elif channel.type != WORK_ITEM:
        out = [
            result(
                "channel",
                False,
                f"'{channel.name}' is a {channel.type} channel",
                code="channel.wrong_type",
                channel=channel.name,
                type=channel.type,
            )
        ]
    else:
        out = [result("channel", True, code="channel.ok", channel=channel.name)]

    asset = next((a for a in config.assets if a.business_address == group.business_address), None)
    if asset is None:
        addresses = ", ".join(a.business_address for a in config.assets) or "none"
        return [
            *out,
            result(
                "asset",
                False,
                f"no asset with business address '{group.business_address}'; existing: {addresses}",
                code="asset.missing",
                address=str(group.business_address),
                available=addresses,
            ),
        ]
    if channel is not None and asset.channel_id != channel.id:
        actual = next((c.name for c in config.channels if c.id == asset.channel_id), "unknown")
        out.append(
            result(
                "asset",
                False,
                f"asset '{asset.name}' is on channel {actual}, not {group.channel}",
                code="asset.wrong_channel",
                asset=asset.name,
                actual=actual,
                channel=str(group.channel),
            )
        )
    else:
        out.append(result("asset", True, f"ok ({asset.name})", code="asset.ok", asset=asset.name))
    out.append(_schema_result(asset, result))

    entry = next((e for e in config.entry_points if e.asset_id == asset.id), None)
    if entry is None or not entry.flow_id:
        if entry is None:
            missing = result(
                "entry point",
                False,
                f"no Work Item entry point for asset '{asset.name}'",
                code="entry.missing",
                asset=asset.name,
            )
        else:
            missing = result(
                "entry point",
                False,
                f"entry point '{entry.name}' has no flow for asset '{asset.name}'",
                code="entry.no_flow",
                entry=entry.name,
            )
        return [*out, missing]
    if not entry.active:
        out.append(
            result(
                "entry point",
                False,
                f"entry point '{entry.name}' is not active",
                code="entry.inactive",
                entry=entry.name,
            )
        )
    else:
        out.append(
            result("entry point", True, f"ok ({entry.name})", code="entry.ok", entry=entry.name)
        )

    flow = config.flows.get(entry.flow_id)
    if flow is None:
        hint = f"the flow of '{entry.name}' could not be exported"
        return [*out, result("flow", False, hint, code="flow.not_exported", entry=entry.name)]
    return [*out, *_flow_results(group, flow, config.queues, sharing, result)]


def _schema_result(asset: Asset, result: Result) -> CheckResult:
    """Compare the asset's schema with the keys OpenPlanner sends."""
    missing = [key for key in WORK_ITEM_FIELDS if key not in asset.fields]
    required = sorted(k for k, (req, _) in asset.fields.items() if req and k not in REQUIRED_FIELDS)
    hidden = [k for k in WORK_ITEM_FIELDS if k in asset.fields and not asset.fields[k][1]]
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if required:
        problems.append(f"requires {', '.join(required)} (only bookingReference may be required)")
    if problems:
        return result(
            "asset schema",
            False,
            "; ".join(problems) + ": Create Task is refused",
            code="schema.refused",
            missing=", ".join(missing) or "-",
            extra=", ".join(required) or "-",
        )
    if hidden:
        return result(
            "asset schema",
            False,
            f"not viewable: {', '.join(hidden)}",
            required=False,
            code="schema.hidden",
            fields=", ".join(hidden),
        )
    return result("asset schema", True, code="schema.ok")


def _flow_results(
    group: GroupRouting,
    flow: FlowSummary,
    queues: tuple[Queue, ...],
    sharing: list[str],
    result: Result,
) -> list[CheckResult]:
    """Check routing variable, branch, queues, naming rule and Screen Pop of the flow."""
    out: list[CheckResult] = []
    if ROUTING_KEY in flow.variables:
        out.append(result("flow routing", True, code="routing.ok"))
        if group.routing_value in flow.strings:
            out.append(result("flow branch", True, code="branch.ok", value=group.routing_value))
        else:
            out.append(
                result(
                    "flow branch",
                    False,
                    f"no branch for '{group.routing_value}': work items go to the default branch",
                    required=False,
                    code="branch.missing",
                    value=group.routing_value,
                )
            )
    elif len(sharing) > 1:
        out.append(
            result(
                "flow routing",
                False,
                f"the flow does not read {ROUTING_KEY}, so "
                f"groups {', '.join(sharing)} all land in the same queue",
                code="routing.shared",
                groups=", ".join(sharing),
            )
        )
    else:
        out.append(
            result(
                "flow routing",
                False,
                f"the flow does not read {ROUTING_KEY}: every "
                "work item of this entry point goes to the same queue",
                required=False,
                code="routing.not_read",
            )
        )

    used = [q for q in queues if q.id in flow.strings]
    wrong = [q for q in used if q.channel_type != WORK_ITEM]
    if not used:
        out.append(
            result("flow queues", False, "the flow queues to no known queue", code="queues.none")
        )
    elif wrong:
        listed = ", ".join(f"{q.name} ({q.channel_type})" for q in wrong)
        out.append(
            result(
                "flow queues",
                False,
                f"not Work Item queues: {listed}",
                code="queues.wrong_type",
                queues=listed,
            )
        )
    else:
        names = ", ".join(q.name for q in used)
        out.append(result("flow queues", True, f"ok ({names})", code="queues.ok", queues=names))

    named = [q.name for q in used if names_match(group.routing_value, q.name)]
    if named:
        out.append(
            result(
                "queue naming",
                True,
                f"ok ({', '.join(named)})",
                code="naming.ok",
                queues=", ".join(named),
            )
        )
    elif used:
        out.append(
            result(
                "queue naming",
                False,
                f"no queue named after '{group.routing_value}' "
                "(rule: the queue name contains the routing value as a name part; "
                "'-', '_' and case alike)",
                required=False,
                code="naming.no_match",
                value=group.routing_value,
            )
        )

    # ADR 0082 point 2: a Case branch on kind == probe ends the group check's probe work item
    # before an agent sees it; without it OpenPlanner ends the probe itself (a warning).
    if PROBE_KIND in flow.strings:
        out.append(result("probe branch", True, code="probe.ok"))
    else:
        out.append(
            result(
                "probe branch",
                False,
                f"no branch for kind '{PROBE_KIND}': the group check ends its probe work item "
                "itself (End Task, cjp:user), or an agent sees it",
                required=False,
                code="probe.missing",
            )
        )

    if any("screen" in activity.lower() for activity in flow.activities):
        out.append(result("screen pop", True, code="screenpop.ok"))
    else:
        out.append(
            result(
                "screen pop",
                False,
                "no Screen Pop: add one with meetingLink",
                required=False,
                code="screenpop.missing",
            )
        )
    return out


async def find_region(
    http: httpx.AsyncClient, token: str, base_url: str, org_id: str
) -> str | None:
    """Return the WxCC API host whose configuration service knows ``org_id``, if any.

    The configured host is tried first; the others only when it answers 404 (the
    organisation lives in another region).
    """
    configured = base_url.rstrip("/")
    others = [f"https://api.wxcc-{r}.cisco.com" for r in REGIONS]
    for host in [configured, *[h for h in others if h != configured]]:
        response = await _get(
            http, token, f"{host}/organization/{org_id}/v2/entry-point", {"pageSize": "1"}
        )
        if response.status_code == 200:
            return host
        if host == configured and response.status_code != 404:
            return None
    return None


async def fetch_config(
    http: httpx.AsyncClient, token: str, base_url: str, org_id: str
) -> WxccConfig:
    """Read channels, assets, Work Item entry points, queues and their flows.

    Raises:
        IntegrationError: ``rejected_<status>`` when a listing is refused.
    """
    base = f"{base_url.rstrip('/')}/organization/{org_id}"
    channels = [
        Channel(str(c["id"]), str(c.get("name", "")), str(c.get("channelType", "")))
        for c in await _list(http, token, f"{base}/v2/channel", "id,name,channelType")
    ]
    assets = [
        Asset(
            str(a["id"]),
            str(a.get("name", "")),
            str(a.get("channelId", "")),
            str(a.get("businessAddress", "")),
            {
                str(f.get("fieldKey")): (bool(f.get("isRequired")), bool(f.get("isViewable")))
                for f in a.get("dataSchema") or []
            },
        )
        # The asset listing refuses attribute selection; only the fields above are kept.
        for a in await _list(http, token, f"{base}/v2/asset", None)
    ]
    entry_points = [
        EntryPoint(
            str(e["id"]),
            str(e.get("name", "")),
            e.get("channelAssetId"),
            e.get("flowId"),
            bool(e.get("active")),
        )
        for e in await _list(
            http,
            token,
            f"{base}/v2/entry-point",
            "id,name,channelType,channelAssetId,flowId,active",
        )
        if e.get("channelType") == WORK_ITEM
    ]
    queues = [
        Queue(str(q["id"]), str(q.get("name", "")), str(q.get("channelType", "")))
        for q in await _list(http, token, f"{base}/v2/contact-service-queue", "id,name,channelType")
    ]
    flows = {
        e.flow_id: await _flow(http, token, base_url, org_id, e.flow_id)
        for e in entry_points
        if e.flow_id
    }
    return WxccConfig(tuple(channels), tuple(assets), tuple(entry_points), tuple(queues), flows)


async def _flow(
    http: httpx.AsyncClient, token: str, base_url: str, org_id: str, flow_id: str
) -> FlowSummary | None:
    """Export the latest version of a flow; ``None`` when it cannot be read."""
    url = f"{base_url.rstrip('/')}/{org_id}/project/{PROJECT_ID}/v2/flows/{flow_id}:export"
    response = await _get(http, token, url, {"version": "latest"})
    if response.status_code != 200:
        return None
    doc = response.json()
    return parse_flow(doc) if isinstance(doc, dict) else None


async def _list(
    http: httpx.AsyncClient, token: str, url: str, attributes: str | None
) -> list[dict[str, Any]]:
    """Return every item of a paged configuration listing."""
    items: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        params = {"page": str(page), "pageSize": str(PAGE_SIZE)}
        if attributes:
            params["attributes"] = attributes
        response = await _get(http, token, url, params)
        if response.status_code != 200:
            raise IntegrationError(f"rejected_{response.status_code}", retryable=False)
        body = response.json()
        data = [item for item in body.get("data") or [] if isinstance(item, dict)]
        items.extend(data)
        pages = int((body.get("meta") or {}).get("totalPages") or 1)
        if not data or page + 1 >= pages:
            break
    return items


async def _get(
    http: httpx.AsyncClient, token: str, url: str, params: dict[str, str]
) -> httpx.Response:
    """GET with the bearer token; network failures become ``unreachable``."""
    try:
        return await http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as error:
        raise IntegrationError("unreachable", retryable=True) from error


async def organisation_id(http: httpx.AsyncClient, token: str, base_url: str) -> str | None:
    """Return the WxCC organisation id the token belongs to (from the subscriptions listing)."""
    response = await _get(http, token, f"{base_url.rstrip('/')}/v2/subscriptions", {})
    if response.status_code != 200:
        return None
    org = (response.json().get("meta") or {}).get("orgId")
    return org if isinstance(org, str) else None


async def region_check(
    http: httpx.AsyncClient, token: str, base_url: str
) -> tuple[CheckResult, str | None, str | None]:
    """Check that ``WXCC_API_BASE_URL`` is the organisation's region.

    Returns the result line, the host that knows the organisation (to continue with) and the
    organisation id.
    """
    org = await organisation_id(http, token, base_url)
    if org is None:
        return (
            CheckResult(
                "WxCC region",
                None,
                False,
                "organisation id unknown: GET /v2/subscriptions failed (cjp:config_read)",
                code="region.no_org",
            ),
            None,
            None,
        )
    found = await find_region(http, token, base_url, org)
    configured = base_url.rstrip("/")
    if found == configured:
        ok = CheckResult(
            "WxCC region",
            200,
            True,
            f"ok ({configured})",
            code="region.ok",
            params=(("host", configured),),
        )
        return ok, found, org
    if found:
        hint = f"the organisation's configuration lives at {found}: set WXCC_API_BASE_URL={found}"
        wrong = CheckResult(
            "WxCC region", 404, False, hint, code="region.wrong", params=(("host", found),)
        )
        return wrong, found, org
    return (
        CheckResult(
            "WxCC region",
            None,
            False,
            "no region's configuration service knows "
            "this organisation (cjp:config_read, Contact Center admin role)",
            code="region.unknown",
        ),
        None,
        org,
    )
