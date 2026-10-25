"""Operator commands: ``wxcc-check``, ``wxcc-config-check`` and ``register-webhooks``."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import GroupRow
from app.adapters.wxcc_config import PROJECT_ID
from app.cli import _register_webhooks, _wxcc_check, _wxcc_config_check
from app.config import Settings
from tests.adapters.db.conftest import seed_group
from tests.support.database import migrated_database

ORG = "11111111-2222-3333-4444-555555555555"

ACCESS_TOKEN = "access-token-never-printed"  # noqa: S105 - test value


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return settings.model_copy(
        update={
            "database_url": migrated_database(tmp_path, monkeypatch, "cli"),
            "dev_settings_key_path": tmp_path / "dev-settings.key",
            "webex_client_id": "client",
            "webex_client_secret": SecretStr("client-secret"),
            "webex_refresh_token": SecretStr("refresh-token"),
            "webex_webhook_secret": SecretStr("webhook-secret"),
            "wxcc_api_base_url": "https://api.wxcc-eu1.cisco.com",
            "wxcc_webhook_secret": SecretStr("wxcc-secret"),
        }
    )


def _transport(seen: list[httpx.Request], wxcc_status: int) -> httpx.MockTransport:
    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})
        if request.url.host == "webexapis.com":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and request.url.path == "/v2/subscriptions":
            return httpx.Response(201, json={"id": "subscription"})
        if request.method == "POST" and request.url.path == "/v2/tasks":
            return httpx.Response(400 if wxcc_status == 200 else wxcc_status)
        if request.method == "POST":
            return httpx.Response(404 if wxcc_status == 200 else wxcc_status)
        return httpx.Response(wxcc_status, json={"data": [], "meta": {"orgId": ORG}})

    return httpx.MockTransport(route)


async def test_wxcc_check_reports_todays_403s_and_fails(configured: Settings) -> None:
    seen: list[httpx.Request] = []

    report, passed = await _wxcc_check(configured, _transport(seen, 403))

    assert not passed
    assert "FAIL  GET /v2/subscriptions" in report
    assert "cjp:config_read" in report
    assert "OK    GET /meetings" in report
    assert ACCESS_TOKEN not in report
    assert "refresh-token" not in report


async def test_wxcc_check_passes_once_the_scopes_are_fixed(configured: Settings) -> None:
    report, passed = await _wxcc_check(configured, _transport([], 200))

    assert passed, report
    assert "FAIL" not in report


async def test_wxcc_check_without_a_service_app_does_nothing(settings: Settings) -> None:
    report, passed = await _wxcc_check(settings)

    assert not passed
    assert "WEBEX_CLIENT_ID" in report


async def test_register_webhooks_uses_the_webhook_base_url(configured: Settings) -> None:
    await _wired_group(configured)
    seen: list[httpx.Request] = []
    tunnel = configured.model_copy(
        update={
            "public_base_url": "http://127.0.0.1:8000",
            "webhook_base_url": "https://abc.trycloudflare.com",
        }
    )

    report = await _register_webhooks(tunnel, _transport(seen, 200))

    assert "meetings/started [host-demo@example.nl]: created" in report
    assert "wxcc subscription: created" in report
    targets = {
        r.content.decode()
        for r in seen
        if r.method == "POST" and r.url.path in ("/v1/webhooks", "/v2/subscriptions")
    }
    assert all("https://abc.trycloudflare.com/webhooks/" in t for t in targets)
    assert not any("127.0.0.1" in t for t in targets)


async def test_register_webhooks_refuses_a_plain_http_base(configured: Settings) -> None:
    report = await _register_webhooks(configured.model_copy(update={"webhook_base_url": None}))

    assert "WEBHOOK_BASE_URL (or PUBLIC_BASE_URL) must be https" in report


async def _wired_group(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    await seed_group(engine, slug="aangifte")
    async with engine.begin() as connection:
        await connection.execute(
            update(GroupRow).values(
                wxcc_channel="videoafspraak-prototype",
                wxcc_business_address="videoafspraak@rijks.video",
                video_host_email="host-demo@example.nl",
            )
        )
    await engine.dispose()


def _recorded_org(request: httpx.Request) -> httpx.Response:
    """The product owner's tenant on 2026-10-25: configuration only in eu2."""
    path = request.url.path
    if request.url.path == "/v1/access_token":
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})
    if path == "/v2/subscriptions":
        return httpx.Response(200, json={"data": [], "meta": {"orgId": ORG}})
    if request.url.host != "api.wxcc-eu2.cisco.com":
        return httpx.Response(404, json={"error": {"reason": "No Organization was found"}})
    listings: dict[str, list[dict[str, Any]]] = {
        "channel": [{"id": "ch", "name": "videoafspraak-prototype", "channelType": "WORK_ITEM"}],
        "asset": [
            {
                "id": "as",
                "name": "videoafspraak-prototype",
                "channelId": "ch",
                "businessAddress": "videoafspraak@rijks.video",
                "dataSchema": [
                    {"fieldKey": "videoafspraak_Naam", "isRequired": True, "isViewable": True},
                    {
                        "fieldKey": "videoafspraak_MeetingURL",
                        "isRequired": True,
                        "isViewable": True,
                    },
                ],
            }
        ],
        "entry-point": [
            {
                "id": "ep",
                "name": "videoafspraak_prototype",
                "channelType": "WORK_ITEM",
                "channelAssetId": "as",
                "flowId": "fl",
                "active": True,
            }
        ],
        "contact-service-queue": [
            {"id": "q", "name": "videoafspraak_prototype", "channelType": "WORK_ITEM"}
        ],
    }
    if path == f"/{ORG}/project/{PROJECT_ID}/v2/flows/fl:export":
        return httpx.Response(200, json={"variables": [], "nodes": [], "edges": [], "q": "q"})
    kind = path.rsplit("/", 1)[-1]
    return httpx.Response(200, json={"data": listings.get(kind, []), "meta": {"totalPages": 1}})


async def test_wxcc_config_check_finds_the_region_and_the_schema_mismatch(
    configured: Settings,
) -> None:
    await _wired_group(configured)

    report, passed = await _wxcc_config_check(configured, httpx.MockTransport(_recorded_org))

    assert not passed
    assert "set WXCC_API_BASE_URL=https://api.wxcc-eu2.cisco.com" in report
    assert "OK    [aangifte] channel" in report
    assert "FAIL  [aangifte] asset schema" in report
    assert "videoafspraak_MeetingURL" in report
    assert "WARN  [aangifte] flow routing" in report
    assert ACCESS_TOKEN not in report


async def test_wxcc_config_check_without_wired_groups_does_nothing(configured: Settings) -> None:
    report, passed = await _wxcc_config_check(configured, httpx.MockTransport(_recorded_org))

    assert not passed
    assert "No group has a WxCC channel and business address" in report


async def test_wxcc_check_reports_a_wrong_region(configured: Settings) -> None:
    report, passed = await _wxcc_check(configured, httpx.MockTransport(_recorded_org))

    assert not passed
    assert "FAIL  WxCC region" in report
