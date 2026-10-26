"""``wxcc-check`` probes: status per call, the likely missing scope, never a token or a body.

The responses are recorded from the product owner's environment on 2026-10-24 (status codes
only; bodies replaced by the documented error shape).
"""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.adapters.diagnostics import (
    NIL_TASK_ID,
    CheckResult,
    format_report,
    passed,
    probes,
    run_checks,
)
from app.adapters.webex_auth import ServiceAppConfig, WebexTokens
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import MemoryTokenStore

WEBEX = "https://webexapis.com/v1"
WXCC = "https://api.wxcc-eu1.cisco.com"
SECRET_TOKEN = "access-token-value-never-printed"  # noqa: S105 - test value
BODY_WITH_PII = {"items": [{"hostEmail": "host@example.nl", "title": "Ada Lovelace"}]}


CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))


def _client(
    route: Callable[[httpx.Request], httpx.Response],
) -> tuple[WebexTokens, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    tokens = WebexTokens(ServiceAppConfig("c", "s", "r"), http, MemoryTokenStore(), CLOCK)
    return tokens, http


def _tokens(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WebexTokens, httpx.AsyncClient]:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": SECRET_TOKEN, "expires_in": 3600})
        assert request.headers["authorization"] == f"Bearer {SECRET_TOKEN}"
        return handler(request)

    return _client(route)


async def _check(handler: Callable[[httpx.Request], httpx.Response]) -> list[CheckResult]:
    tokens, http = _tokens(handler)
    return await run_checks(http, tokens, probes(WEBEX, WXCC), CLOCK)


def _today(request: httpx.Request) -> httpx.Response:
    """The real environment on 2026-10-24: Webex works, every WxCC call is refused."""
    if request.url.host == "webexapis.com":
        return httpx.Response(200, json=BODY_WITH_PII)
    return httpx.Response(403, json={"error": {"key": "403", "message": ["Forbidden"]}})


async def test_todays_environment_reports_the_missing_wxcc_scopes() -> None:
    results = await _check(_today)

    by_name = {r.name: r for r in results}
    assert by_name["token refresh"].ok
    assert by_name["GET /meetings"].status == 200
    assert by_name["GET /meetings"].ok
    subscriptions = by_name["GET /v2/subscriptions"]
    assert (subscriptions.status, subscriptions.ok) == (403, False)
    assert "cjp:config_read" in subscriptions.hint
    assert "cjp:config_read" in by_name["GET /v2/event-types"].hint
    assert by_name["GET /v2/event-types"].required
    assert "cjp:task_write" in by_name["POST /v2/tasks (empty body)"].hint
    assert "cjp:user" in by_name["POST /v1/tasks/{nil}/end"].hint


async def test_harmless_probes_count_validation_errors_as_authorised() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            # Authorised but invalid: the empty work item and the unknown task are refused.
            return httpx.Response(400 if request.url.path == "/v2/tasks" else 404)
        return httpx.Response(200, json={"data": []})

    results = await _check(handler)

    assert all(r.ok for r in results), [r for r in results if not r.ok]
    posts = [r for r in seen if r.method == "POST"]
    assert {p.url.path for p in posts} == {
        "/v2/tasks",
        f"/v2/tasks/{NIL_TASK_ID}/messages",
        f"/v1/tasks/{NIL_TASK_ID}/end",
    }
    assert all(p.content in (b"{}", b"") for p in posts)  # nothing is created or ended
    search = next(r for r in seen if r.url.path == "/v1/tasks")
    assert search.url.params["pageSize"] == "1"
    assert "channelTypes" not in search.url.params  # workitem is not a searchable type


async def test_refused_refresh_token_skips_the_api_calls() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/access_token", "no API call without a token"
        return httpx.Response(400, json={"error": "invalid_grant"})

    tokens, http = _client(route)

    results = await run_checks(http, tokens, probes(WEBEX, WXCC), CLOCK)

    assert [(r.name, r.ok) for r in results] == [("token refresh", False)]
    assert "WEBEX_REFRESH_TOKEN" in results[0].hint


async def test_unreachable_host_is_reported_without_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed for secret-host")

    tokens, http = _tokens(handler)

    results = await run_checks(http, tokens, probes(WEBEX, None), FixedClock(datetime.now(UTC)))

    meetings = next(r for r in results if r.name == "GET /meetings")
    assert (meetings.status, meetings.ok, meetings.hint) == (
        None,
        False,
        "unreachable (ConnectError)",
    )
    assert not any(r.name.startswith(("GET /v2", "POST")) for r in results)  # no WxCC host


async def test_a_401_retries_once_with_a_fresh_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401 if len(calls) == 1 else 200, json={})

    tokens, http = _tokens(handler)

    results = await run_checks(http, tokens, probes(WEBEX, None)[:1], FixedClock(datetime.now(UTC)))

    assert calls == ["/v1/meetings", "/v1/meetings"]
    assert results[-1].ok


def test_report_has_status_and_hint_but_no_token_or_body() -> None:
    results = [
        CheckResult("token refresh", None, True, "ok"),
        CheckResult("GET /meetings", 200, True, "ok"),
        CheckResult("GET /v2/subscriptions", 403, False, "needs cjp:config_read"),
    ]

    report = format_report(results)

    assert "GET /v2/subscriptions" in report
    assert "403" in report
    assert "cjp:config_read" in report
    assert "FAIL" in report
    assert "OK" in report
    assert SECRET_TOKEN not in report
    assert "Ada" not in report
    assert "host@" not in report


async def test_v2_is_primary_and_v1_probes_only_warn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/") and request.url.host != "webexapis.com":
            return httpx.Response(403)
        if request.method == "POST":
            return httpx.Response(400)
        return httpx.Response(200, json={"data": []})

    results = await _check(handler)

    names = [r.name for r in results]
    assert names.index("POST /v2/tasks (empty body)") < names.index("POST /v1/tasks/{nil}/end")
    assert passed(results)
    report = format_report(results)
    assert "WARN  POST /v1/tasks/{nil}/end" in report
    assert "FAIL" not in report
