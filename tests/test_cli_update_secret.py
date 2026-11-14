"""``register-webhooks --update-secret`` gives a rotated installation secret to what exists.

ADR 0124 point 8: the video platform's webhooks are updated in place, WxCC's subscription renewed.
"""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import GroupRow
from app.cli import _register_webhooks
from app.config import Settings
from tests.adapters.db.conftest import seed_group
from tests.fakes.tokens import ACCESS_TOKEN
from tests.support.database import migrated_database

HOST = "host-demo@example.nl"
EVENTS = (
    ("meetings", "started"),
    ("meetings", "ended"),
    ("meetingParticipants", "joined"),
    ("meetingParticipants", "left"),
)


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return settings.model_copy(
        update={
            "database_url": migrated_database(tmp_path, monkeypatch, "cli-update-secret"),
            "dev_settings_key_path": tmp_path / "dev-settings.key",
            "webex_client_id": "client",
            "webex_client_secret": SecretStr("client-secret"),
            "webex_refresh_token": SecretStr("refresh-token"),
            "webex_webhook_secret": SecretStr("new-webex-secret"),
            "wxcc_api_base_url": "https://api.wxcc-eu1.cisco.com",
            "wxcc_webhook_secret": SecretStr("new-wxcc-secret"),
            "webhook_base_url": "https://hooks.example.nl",
        }
    )


async def _wire_a_group(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    await seed_group(engine, slug="balie")
    async with engine.begin() as connection:
        await connection.execute(
            update(GroupRow).values(
                wxcc_channel="videoafspraak-prototype",
                wxcc_business_address="videoafspraak@example.video",
                video_host_email=HOST,
            )
        )
    await engine.dispose()


def _platform(seen: list[httpx.Request]) -> httpx.MockTransport:
    hooks = {
        "items": [
            {
                "id": f"w-{event}",
                "targetUrl": "https://hooks.example.nl/webhooks/webex",
                "resource": resource,
                "event": event,
                "filter": f"hostEmail={HOST}",
            }
            for resource, event in EVENTS
        ]
    }
    subscriptions = {
        "data": [
            {
                "id": "old-1",
                "destinationUrl": "https://hooks.example.nl/webhooks/wxcc",
            }
        ]
    }

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})
        if request.url.host == "webexapis.com":
            return httpx.Response(200, json=hooks)
        if request.method == "GET":
            return httpx.Response(200, json=subscriptions)
        return httpx.Response(201, json={"id": "x"})

    return httpx.MockTransport(route)


async def test_the_flag_updates_the_video_webhooks_in_place_and_renews_the_subscription(
    configured: Settings,
) -> None:
    await _wire_a_group(configured)
    seen: list[httpx.Request] = []

    report = await _register_webhooks(configured, _platform(seen), update_secret=True)

    assert f"meetings/started [{HOST}]: updated" in report
    assert "wxcc subscription: renewed" in report
    puts = [r for r in seen if r.method == "PUT"]
    assert len(puts) == 4
    assert all(b"new-webex-secret" in r.content for r in puts)
    wxcc = [r for r in seen if r.url.host == "api.wxcc-eu1.cisco.com" and r.method != "GET"]
    assert [r.method for r in wxcc] == ["POST", "DELETE"]  # the new subscription first
    assert "new-webex-secret" not in report
    assert "new-wxcc-secret" not in report


async def test_without_the_flag_what_exists_is_left_alone(configured: Settings) -> None:
    await _wire_a_group(configured)
    seen: list[httpx.Request] = []

    report = await _register_webhooks(configured, _platform(seen))

    assert f"meetings/started [{HOST}]: exists" in report
    assert "wxcc subscription: exists" in report
    changes = [r for r in seen if r.method in ("PUT", "POST", "DELETE")]
    assert [r.url.path for r in changes] == ["/v1/access_token"]  # only the sign-in
