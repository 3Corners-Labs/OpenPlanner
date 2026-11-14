"""The Service App check behind ``wxcc-check`` and the button on Koppelingen (ADR 0082)."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.adapters.diagnostics import CheckResult
from app.adapters.service_app_check import (
    SDK_FILE_CHECK,
    report_body,
    results_from_report,
    service_app_check,
)
from app.adapters.vendored_files import VendoredFile
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import webex_tokens

WEBEX = "https://webexapis.com/v1"
WXCC = "https://api.wxcc-eu1.cisco.com"
CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))

LINES = [
    CheckResult("token refresh", None, True, "ok"),
    CheckResult("GET /meetings", 200, True, "ok"),
    CheckResult("POST /v1/tasks/{nil}/end", 403, False, "forbidden: needs cjp:user", False),
]


async def test_without_a_service_app_the_check_is_one_failed_line() -> None:
    results = await service_app_check(httpx.AsyncClient(), None, WEBEX, WXCC, CLOCK)

    assert [(r.name, r.ok) for r in results] == [("token refresh", False)]
    assert "not configured" in results[0].hint


async def test_the_probes_run_and_the_region_check_follows_a_working_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(400 if request.url.path == "/v2/tasks" else 404)
        return httpx.Response(200, json={"data": [], "items": []})

    tokens, http = webex_tokens(handler, CLOCK)

    results = await service_app_check(http, tokens, WEBEX, WXCC, CLOCK)

    assert results[0].name == "token refresh"
    assert results[-1].name == "WxCC region"
    assert "/v2/tasks" in seen


async def test_the_guest_token_is_asked_for_only_when_the_embedded_call_is_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403 if request.url.path == "/v1/guests/token" else 200, json={})

    tokens, http = webex_tokens(handler, CLOCK)

    off = await service_app_check(http, tokens, WEBEX, None, CLOCK)
    on = await service_app_check(http, tokens, WEBEX, None, CLOCK, embedded_call_enabled=True)

    assert not [r for r in off if r.name == "POST /guests/token"]
    assert [r.required for r in on if r.name == "POST /guests/token"] == [True]


async def test_the_sdk_file_is_judged_only_while_the_embedded_call_is_on(tmp_path: Path) -> None:
    folder = tmp_path / "webex"
    folder.mkdir()
    (folder / "meetings.min.js").write_bytes(b"x")
    manifest = {"name": "w", "version": "1", "source": "s", "file": "meetings.min.js"}
    (folder / "manifest.json").write_text(json.dumps(manifest))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    tokens, http = webex_tokens(handler, CLOCK)
    sdk = VendoredFile(folder)

    off = await service_app_check(http, tokens, WEBEX, None, CLOCK, sdk=sdk)
    on = await service_app_check(
        http, tokens, WEBEX, None, CLOCK, embedded_call_enabled=True, sdk=sdk
    )
    without_service_app = await service_app_check(
        http, None, WEBEX, None, CLOCK, embedded_call_enabled=True, sdk=sdk
    )

    assert not [r for r in off if r.name == SDK_FILE_CHECK]
    line = [r for r in on if r.name == SDK_FILE_CHECK]
    assert [(r.ok, r.required) for r in line] == [(False, True)]
    assert "no checksum" in line[0].hint
    assert "docs/runbook.md" in line[0].hint
    assert [r.name for r in without_service_app] == ["token refresh", SDK_FILE_CHECK]
    # A whole file passes; the hint carries no path and no checksum.
    manifest["checksum"] = {"version": "1", "sha256": hashlib.sha256(b"x").hexdigest()}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    whole = await service_app_check(
        http, tokens, WEBEX, None, CLOCK, embedded_call_enabled=True, sdk=sdk
    )
    assert [(r.ok, r.hint) for r in whole if r.name == SDK_FILE_CHECK] == [(True, "ok")]


def test_a_report_round_trips_without_tokens_or_bodies() -> None:
    body = report_body(LINES, wxcc_checked=True)

    assert body["passed"] is True  # the v1 probe is secondary
    assert body["wxcc"] is True
    assert results_from_report(body) == LINES
    assert results_from_report({"results": [{"status": 200}, "junk"]}) == []
    assert "token" not in str(body).lower().replace("token refresh", "")
