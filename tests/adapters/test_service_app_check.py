"""The Service App check behind ``wxcc-check`` and the button on Koppelingen (ADR 0082)."""

import httpx

from app.adapters.diagnostics import CheckResult
from app.adapters.service_app_check import (
    report_body,
    results_from_report,
    service_app_check,
)
from tests.adapters.test_diagnostics import CLOCK, WEBEX, WXCC, _tokens

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

    tokens, http = _tokens(handler)

    results = await service_app_check(http, tokens, WEBEX, WXCC, CLOCK)

    assert results[0].name == "token refresh"
    assert results[-1].name == "WxCC region"
    assert "/v2/tasks" in seen


def test_a_report_round_trips_without_tokens_or_bodies() -> None:
    body = report_body(LINES, wxcc_checked=True)

    assert body["passed"] is True  # the v1 probe is secondary
    assert body["wxcc"] is True
    assert results_from_report(body) == LINES
    assert results_from_report({"results": [{"status": 200}, "junk"]}) == []
    assert "token" not in str(body).lower().replace("token refresh", "")
