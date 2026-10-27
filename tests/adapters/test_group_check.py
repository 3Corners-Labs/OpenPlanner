"""PlatformChecker: one group's chain at Webex and WxCC, as findings (verify button)."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.adapters import group_check
from app.adapters.diagnostics import CheckResult
from app.adapters.group_check import CheckerConfig, PlatformChecker
from app.adapters.webex_auth import ServiceAppConfig, WebexTokens
from app.adapters.webhook_registration import WEBEX_EVENTS
from app.domain.checks import CheckStatus, Finding
from app.domain.ports import IntegrationError
from tests.adapters.test_wxcc_config import _config
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import MemoryTokenStore
from tests.support.domain import make_group

BASE = "https://op.example.nl"
WEBEX = "https://webexapis.example/v1"
WXCC = "https://api.wxcc-eu2.cisco.com"
HOST = "host@example.nl"
WEBEX_SECRET = "webex-secret"
WXCC_SECRET = "wxcc-secret"
CONFIG = CheckerConfig(WEBEX, WXCC, BASE, WEBEX_SECRET, WXCC_SECRET)
NOW = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)

GROUP = make_group(
    id=1,
    organisation_id=1,
    slug="aangifte",
    name="Aangifte",
    video_host_email=HOST,
    wxcc_channel="videoafspraak-prototype",
    wxcc_business_address="videoafspraak@rijks.video",
)


def _hooks() -> list[dict[str, Any]]:
    return [
        {
            "id": f"h{i}",
            "name": f"OpenPlanner {resource} {event}",
            "targetUrl": f"{BASE}/webhooks/webex",
            "resource": resource,
            "event": event,
            "filter": f"hostEmail={HOST}",
            "secret": "x",
            "status": "active",
            "ownedBy": "org",
        }
        for i, (resource, event) in enumerate(WEBEX_EVENTS)
    ]


def _subscriptions() -> list[dict[str, Any]]:
    return [
        {
            "id": "s1",
            "name": "OpenPlanner task events",
            "destinationUrl": f"{BASE}/webhooks/wxcc",
            "eventTypes": ["task:new", "task:connected", "task:ended", "task:failed"],
            "status": "active",
            "secret": "x",
        }
    ]


def _verify_webex(request: httpx.Request) -> httpx.Response:
    expected = hmac.new(WEBEX_SECRET.encode(), request.content, hashlib.sha1).hexdigest()  # noqa: S324
    ok = hmac.compare_digest(expected, request.headers.get("x-spark-signature", ""))
    return httpx.Response(204 if ok else 401)


def _verify_wxcc(request: httpx.Request) -> httpx.Response:
    expected = hmac.new(WXCC_SECRET.encode(), request.content, hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expected, request.headers.get("x-webexcc-signature", ""))
    return httpx.Response(204 if ok else 401)


class Platforms:
    """A mock Webex, WxCC and OpenPlanner; tweak the attributes per test."""

    def __init__(self) -> None:
        self.host_status = 200
        self.hooks = _hooks()
        self.subscriptions = _subscriptions()
        self.webhook_status: int | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.url.path.endswith("/access_token"):
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
        if url.startswith(f"{WEBEX}/meetings"):
            return httpx.Response(self.host_status, json={"items": []})
        if url.startswith(f"{WEBEX}/webhooks"):
            return httpx.Response(200, json={"items": self.hooks})
        if url.startswith(f"{WXCC}/v2/subscriptions"):
            return httpx.Response(200, json={"data": self.subscriptions})
        if url.startswith(BASE):
            if self.webhook_status is not None:
                return httpx.Response(self.webhook_status)
            return _verify_webex(request) if url.endswith("/webex") else _verify_wxcc(request)
        return httpx.Response(404)


def _checker(platforms: Platforms, config: CheckerConfig = CONFIG) -> PlatformChecker:
    http = httpx.AsyncClient(transport=httpx.MockTransport(platforms))
    clock = FixedClock(NOW)
    tokens = WebexTokens(ServiceAppConfig("c", "s", "r"), http, MemoryTokenStore(), clock)
    return PlatformChecker(http, tokens, config, clock)


@pytest.fixture
def wxcc_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """WxCC's configuration matches the group (recorded shapes, OpenPlanner's schema)."""

    async def region(*_: object) -> tuple[CheckResult, str, str]:
        line = CheckResult(
            "WxCC region", 200, True, "ok", code="region.ok", params=(("host", WXCC),)
        )
        return line, WXCC, "org-1"

    async def fetch(*_: object) -> object:
        return _config()

    monkeypatch.setattr(group_check, "region_check", region)
    monkeypatch.setattr(group_check, "fetch_config", fetch)


def _codes(findings: list[Finding]) -> dict[str, CheckStatus]:
    return {f.code: f.status for f in findings}


@pytest.mark.usefixtures("wxcc_ok")
async def test_a_complete_chain_is_all_ok() -> None:
    findings = await _checker(Platforms()).check_group(GROUP, [GROUP])
    codes = _codes(findings)

    assert codes["webex.host_ok"] is CheckStatus.OK
    assert codes["wxcc.region.ok"] is CheckStatus.OK
    assert codes["wxcc.channel.ok"] is CheckStatus.OK
    assert codes["wxcc.schema.ok"] is CheckStatus.OK
    assert codes["webhooks.webex_ok"] is CheckStatus.OK
    assert codes["webhooks.wxcc_ok"] is CheckStatus.OK
    assert codes["webhooks.delivery_ok"] is CheckStatus.OK
    # Warnings only for what the recorded flow lacks (routing variable, Screen Pop).
    assert {c for c, s in codes.items() if s is CheckStatus.FAIL} == set()
    assert findings[0].params == (("host", HOST),)


@pytest.mark.usefixtures("wxcc_ok")
async def test_nothing_is_created_or_deleted_and_no_token_leaves_for_our_endpoints() -> None:
    platforms = Platforms()
    await _checker(platforms).check_group(GROUP, [GROUP])

    assert {r.method for r in platforms.requests if not str(r.url).startswith(BASE)} <= {
        "GET",
        "POST",  # only the token refresh
    }
    assert all(
        r.url.path.endswith("/access_token")
        for r in platforms.requests
        if r.method == "POST" and not str(r.url).startswith(BASE)
    )
    assert not any(
        "authorization" in r.headers for r in platforms.requests if str(r.url).startswith(BASE)
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "webex.host_unknown"), (400, "webex.host_unknown"), (403, "webex.host_refused")],
)
@pytest.mark.usefixtures("wxcc_ok")
async def test_a_host_webex_does_not_know_or_refuses_fails(status: int, code: str) -> None:
    platforms = Platforms()
    platforms.host_status = status

    findings = await _checker(platforms).check_group(GROUP, [GROUP])

    assert findings[0].code == code
    assert findings[0].status is CheckStatus.FAIL
    assert ("error", f"rejected_{status}") in findings[0].params


@pytest.mark.usefixtures("wxcc_ok")
async def test_without_host_or_wxcc_mapping_the_check_warns() -> None:
    bare = replace(GROUP, video_host_email=None, wxcc_channel=None, wxcc_business_address=None)

    codes = _codes(await _checker(Platforms()).check_group(bare, [bare]))

    assert codes["webex.no_host"] is CheckStatus.WARN
    assert codes["wxcc.no_mapping"] is CheckStatus.WARN
    assert "webhooks.webex_ok" not in codes  # no host, so no host webhooks to expect


async def test_without_wxcc_the_routing_is_skipped() -> None:
    config = replace(CONFIG, wxcc_base_url=None)

    codes = _codes(await _checker(Platforms(), config).check_group(GROUP, [GROUP]))

    assert codes["wxcc.not_configured"] is CheckStatus.SKIPPED
    assert "webhooks.wxcc_ok" not in codes


async def test_a_refused_wxcc_configuration_is_a_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*_: object) -> object:
        raise IntegrationError("rejected_403", retryable=False)

    monkeypatch.setattr(group_check, "region_check", refused)

    codes = _codes(await _checker(Platforms()).check_group(GROUP, [GROUP]))

    assert codes["wxcc.config_refused"] is CheckStatus.FAIL


@pytest.mark.usefixtures("wxcc_ok")
async def test_missing_and_stale_registrations_are_reported() -> None:
    platforms = Platforms()
    platforms.hooks = [h for h in _hooks() if h["resource"] == "meetings"]
    platforms.hooks.append(
        {**_hooks()[0], "id": "old", "targetUrl": "https://old.example/webhooks/webex"}
    )
    platforms.subscriptions = []

    findings = await _checker(platforms).check_group(GROUP, [GROUP])
    by_code = {f.code: f for f in findings}

    assert by_code["webhooks.webex_missing"].status is CheckStatus.WARN
    assert by_code["webhooks.webex_missing"].params == (
        ("events", "meetingParticipants/joined, meetingParticipants/left"),
    )
    assert by_code["webhooks.wxcc_missing"].status is CheckStatus.FAIL
    assert by_code["webhooks.stale"].params == (("count", "1"),)


@pytest.mark.usefixtures("wxcc_ok")
async def test_a_dead_tunnel_fails_the_delivery() -> None:
    platforms = Platforms()
    platforms.webhook_status = 502

    findings = await _checker(platforms).check_group(GROUP, [GROUP])
    failed = [f for f in findings if f.code == "webhooks.delivery_failed"]

    assert failed
    assert ("outcome", "proxy_error") in failed[0].params
    assert "webhooks.delivery_ok" not in _codes(findings)


async def test_a_base_url_without_https_stops_the_webhook_checks() -> None:
    config = replace(CONFIG, webhook_base="http://127.0.0.1:8000", wxcc_base_url=None)

    findings = await _checker(Platforms(), config).check_group(GROUP, [GROUP])

    assert [f.code for f in findings if f.area.value == "webhooks"] == ["webhooks.not_https"]


@pytest.mark.usefixtures("wxcc_ok")
async def test_groups_sharing_an_asset_are_named_only_from_the_peers_given() -> None:
    twin = replace(GROUP, id=2, slug="vergunning", wxcc_routing_value="vergunning")

    findings = await _checker(Platforms()).check_group(GROUP, [GROUP, twin])
    shared = [f for f in findings if f.code == "wxcc.routing.shared"]

    assert shared
    assert shared[0].params == (("groups", "aangifte, vergunning"),)
    assert json.dumps([f.params for f in findings])  # params are plain strings
