"""Operator commands: checks, webhook registration, listing, reset and delivery self-test."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import GroupRow
from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore, StoredReport
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.wxcc_config import PROJECT_ID
from app.cli import (
    _register_webhooks,
    _seed_closures,
    _webhooks_health,
    _webhooks_list,
    _webhooks_reset,
    _wxcc_check,
    _wxcc_config_check,
    main,
)
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


TUNNEL = "https://issues-tvs-chronicle-exact.trycloudflare.com"


def _registrations(seen: list[httpx.Request]) -> httpx.MockTransport:
    """Webex and WxCC as on 2026-10-25: current, unfiltered and old-tunnel registrations."""
    hook = {"resource": "meetings", "event": "started", "secret": "s", "status": "active"}
    webhooks = [
        hook
        | {
            "id": "current",
            "targetUrl": f"{TUNNEL}/webhooks/webex",
            "filter": "hostEmail=host-demo@example.nl",
        },
        hook | {"id": "unfiltered", "targetUrl": f"{TUNNEL}/webhooks/webex"},
        hook
        | {
            "id": "old",
            "targetUrl": "https://old.trycloudflare.com/webhooks/webex",
            "filter": "hostEmail=host-demo@example.nl",
        },
    ]
    subscription = {
        "id": "sub",
        "destinationUrl": f"{TUNNEL}/webhooks/wxcc",
        "eventTypes": ["task:new", "task:connected", "task:ended", "task:failed"],
    }

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/access_token":
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "expires_in": 3600,
                    "refresh_token": "rotated",
                    "refresh_token_expires_in": 7_776_000,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(403 if request.url.path.endswith("unfiltered") else 204)
        if request.method == "POST":
            return httpx.Response(201, json={"id": "new"})
        if request.url.path == "/v1/webhooks":
            return httpx.Response(200, json={"items": webhooks})
        if request.url.path == "/v2/subscriptions":
            return httpx.Response(200, json={"data": [subscription]})
        return httpx.Response(404)

    return httpx.MockTransport(route)


@pytest.fixture
def tunnelled(configured: Settings) -> Settings:
    return configured.model_copy(update={"webhook_base_url": TUNNEL})


async def test_webhooks_list_classifies_and_stores_the_result(tunnelled: Settings) -> None:
    await _wired_group(tunnelled)

    report, ok = await _webhooks_list(tunnelled, _registrations([]))

    assert not ok
    assert "CURRENT  webex  meetings/started" in report
    assert "no hostEmail filter" in report
    assert "points at another address" in report
    assert "MISSING  webex meetings/ended [host-demo@example.nl]" in report
    assert ACCESS_TOKEN not in report
    stored = await _stored(tunnelled, REGISTRATIONS_REPORT)
    assert stored is not None
    assert stored.body["base"] == TUNNEL
    assert len(stored.body["registrations"]) == 4


async def test_webhooks_reset_is_a_dry_run_without_yes(tunnelled: Settings) -> None:
    await _wired_group(tunnelled)
    seen: list[httpx.Request] = []

    report, ok = await _webhooks_reset(
        tunnelled, _registrations(seen), confirmed=False, reregister=True
    )

    assert ok
    assert not [r for r in seen if r.method in ("DELETE", "POST") and "webhooks" in r.url.path]
    assert "would delete webex meetings/started unfiltered" in report
    assert "would delete webex meetings/started old" in report
    assert "Dry run: nothing deleted. Run again with --yes" in report
    assert "only together with --yes" in report


async def test_webhooks_reset_with_yes_deletes_stale_and_reports_refusals(
    tunnelled: Settings,
) -> None:
    await _wired_group(tunnelled)
    seen: list[httpx.Request] = []

    report, ok = await _webhooks_reset(
        tunnelled, _registrations(seen), confirmed=True, reregister=True
    )

    assert not ok
    deleted = sorted(r.url.path for r in seen if r.method == "DELETE")
    assert deleted == ["/v1/webhooks/old", "/v1/webhooks/unfiltered"]
    assert "unfiltered: refused (rejected_403): Webex quirk" in report
    assert "deleted webex meetings/started old" in report
    assert "meetings/ended [host-demo@example.nl]: created" in report
    assert await _stored(tunnelled, REGISTRATIONS_REPORT) is not None


async def test_webhooks_health_reports_tokens_and_deliveries(tunnelled: Settings) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "issues-tvs-chronicle-exact.trycloudflare.com":
            return httpx.Response(530)  # Cloudflare: the tunnel is down
        return _registrations([]).handle_request(request)

    report, ok = await _webhooks_health(tunnelled, httpx.MockTransport(route))

    assert not ok
    assert "OK    token refresh" in report
    assert "refresh token" in report
    assert "FAIL  POST /webhooks/webex (signed)" in report
    assert "proxy or tunnel error" in report
    assert ACCESS_TOKEN not in report
    assert "webhook-secret" not in report
    stored = await _stored(tunnelled, HEALTH_REPORT)
    assert stored is not None
    assert {d["status"] for d in stored.body["deliveries"]} == {530}


async def test_webhooks_health_warns_about_a_plain_http_base(configured: Settings) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "testserver":
            return httpx.Response(401)
        return _registrations([]).handle_request(request)

    report, ok = await _webhooks_health(
        configured.model_copy(update={"public_base_url": "http://testserver"}),
        httpx.MockTransport(route),
    )

    assert not ok
    assert "FAIL  WEBHOOK_BASE_URL" in report


async def test_webhook_commands_without_a_service_app_do_nothing(settings: Settings) -> None:
    listed, list_ok = await _webhooks_list(settings)
    reset, reset_ok = await _webhooks_reset(settings, confirmed=True)

    assert not list_ok
    assert not reset_ok
    assert "WEBEX_CLIENT_ID" in listed
    assert "WEBEX_CLIENT_ID" in reset


async def _stored(settings: Settings, name: str) -> StoredReport | None:
    engine = create_engine(settings.database_url)
    try:
        return await ReportStore(engine).get(name)
    finally:
        await engine.dispose()


def test_admin_map_prints_the_navigation_register(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["admin-map"]) == 0

    doc = Path(__file__).resolve().parents[1] / "docs" / "admin-map.md"
    assert capsys.readouterr().out == doc.read_text()


@pytest.fixture
def migrated(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return settings.model_copy(
        update={"database_url": migrated_database(tmp_path, monkeypatch, "cli")}
    )


async def test_seed_closures_prints_the_example_pages_as_json(migrated: Settings) -> None:
    import json

    report, ok = await _seed_closures(migrated)

    paths = json.loads(report)
    assert ok
    assert paths["closed_group"] == "/g/demo-gesloten"
    assert paths["planned_closure"] == "/g/demo-gepland/closure"
    assert paths["suspended_booking"].startswith("/b/")


async def test_seed_closures_refuses_production(settings: Settings) -> None:
    report, ok = await _seed_closures(settings.model_copy(update={"environment": "prod"}))

    assert not ok
    assert report == "seed-closures adds a made-up booking; it runs only outside production\n"


def test_client_pseudonym_prints_the_pseudonym_of_an_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY", "k" * 40)

    assert main(["client-pseudonym", "203.0.113.7"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["client-pseudonym", "not-an-address"]) == 1

    assert len(first) == 16
    assert "203.0.113.7" not in first


async def test_tls_check_stores_the_public_address_and_fails_on_a_finding(
    migrated: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    # ADR 0064: the test settings use http, so there is no certificate to check.
    from app.cli import _tls_check
    from app.domain.tls_check import CertificateStatus
    from tests.fakes.tls import FakeCertificateProbe

    report, ok = await _tls_check(migrated, probe=FakeCertificateProbe())

    assert not ok
    assert "finding     not_https" in report
    engine = create_engine(migrated.database_url)
    try:
        stored = await CertificateStatus(SqlUnitOfWork(engine)).latest()
    finally:
        await engine.dispose()
    assert stored is not None
    assert stored.host == "127.0.0.1"


async def test_tls_check_of_another_host_reports_the_certificate_and_stores_nothing(
    migrated: Settings,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.cli import _tls_check
    from app.domain.tls import Certificate, ProbeResult, Trust
    from app.domain.tls_check import CertificateStatus
    from tests.fakes.tls import FakeCertificateProbe

    now = datetime.now(UTC)
    probe = FakeCertificateProbe(
        ProbeResult(
            Certificate(
                "booking.test",
                "Pebble Intermediate CA",
                now - timedelta(days=1),
                now + timedelta(days=5),
                ("booking.test",),
            ),
            Trust.UNTRUSTED,
        )
    )

    report, ok = await _tls_check(migrated, "booking.test", 8443, probe=probe)

    assert not ok
    assert probe.asked == [("booking.test", 8443)]
    assert "finding     untrusted" in report
    assert "issuer      Pebble Intermediate CA" in report
    assert "(4 days)" in report
    engine = create_engine(migrated.database_url)
    try:
        assert await CertificateStatus(SqlUnitOfWork(engine)).latest() is None
    finally:
        await engine.dispose()


async def test_purge_overdue_counts_per_group_and_purges(migrated: Settings) -> None:
    from app.cli import _purge_overdue
    from app.domain.services import BookingService
    from tests.adapters.db.conftest import seed_group
    from tests.fakes.clock import FixedClock
    from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book

    engine = create_engine(migrated.database_url)
    try:
        await seed_group(engine, slug="aangifte")
        await book(
            BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY), SLOT_0900
        )
    finally:
        await engine.dispose()

    dry, dry_ok = await _purge_overdue(migrated, dry_run=True)
    real, real_ok = await _purge_overdue(migrated)
    again, _ = await _purge_overdue(migrated)

    assert dry_ok
    assert dry == "aangifte: 1 overdue\n1 bookings overdue (1 for over 48 hours)\n"
    assert real_ok
    assert real.endswith("purged 1 of 1 overdue bookings (0 failed, 1 were over 48 hours)\n")
    assert again.startswith("purged 0 of 0")
