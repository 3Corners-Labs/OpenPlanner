"""Operator commands: ``wxcc-check`` and ``register-webhooks`` against recorded responses."""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.cli import _register_webhooks, _wxcc_check
from app.config import Settings
from tests.support.database import migrated_database

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
        return httpx.Response(wxcc_status, json={"data": []})

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
    seen: list[httpx.Request] = []
    tunnel = configured.model_copy(
        update={
            "public_base_url": "http://127.0.0.1:8000",
            "webhook_base_url": "https://abc.trycloudflare.com",
        }
    )

    report = await _register_webhooks(tunnel, _transport(seen, 200))

    assert "meetings/started: created" in report
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
