"""``wxcc-check``: try every kind of call OpenPlanner makes to Webex and WxCC, harmlessly.

Each probe is a read, or a write that cannot change anything (an empty work item that fails
validation, ending a task id that does not exist). What a probe proves is its status: a 400 or
404 on a harmless write means the token was accepted for that API; a 401 or 403 means a scope
or an authorisation is missing, and the probe names the likely one (docs/webhooks.md).

The work item API is v2 (beta) and v2 is primary: Webex and the v2 probes decide whether the
check passes. v1 probes (End Task, which has no v2 form, event types, task search) are
secondary: OpenPlanner works without them, so they print ``WARN`` instead of ``FAIL``.

Only the status and a hint are reported. Tokens, secrets and response bodies (which can hold
names and email addresses of the organisation's users) are never printed or logged.

    token refresh ─► GET /meetings ─► GET /webhooks
                  └► v2: GET /v2/subscriptions ─► POST /v2/tasks {}
                         ─► POST /v2/tasks/{nil}/messages {}
                     v1 (secondary): POST /v1/tasks/{nil}/end ─► event types ─► task search
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from app.adapters.webex_auth import WebexTokens
from app.domain.ports import Clock, IntegrationError

NIL_TASK_ID = "00000000-0000-0000-0000-000000000000"
"""A task id that cannot exist: ending it tests the scope without touching a real task."""

AUTHORISED_HINT = "an administrator authorised the Service App in Control Hub"


@dataclass(frozen=True)
class Probe:
    """One harmless call and what a refusal of it most likely means."""

    name: str
    method: str
    url: str
    scope_hint: str
    params: dict[str, str] = field(default_factory=dict)
    json: dict[str, Any] | None = None
    ok: tuple[int, ...] = (200,)
    last_hour: bool = False
    """Add ``from``/``to`` (epoch ms) for the last hour, as the task search requires."""
    required: bool = True
    """Whether a failure fails the check (v1 probes are secondary)."""


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one probe: HTTP status (``None`` without a response) and a hint."""

    name: str
    status: int | None
    ok: bool
    hint: str
    required: bool = True


def probes(webex_base_url: str, wxcc_base_url: str | None) -> list[Probe]:
    """Return the probes for the configured hosts; WxCC probes only with a WxCC host."""
    webex = webex_base_url.rstrip("/")
    found = [
        Probe(
            "GET /meetings",
            "GET",
            f"{webex}/meetings",
            "meeting:admin_schedule_read (and meeting:admin_schedule_write to create meetings)",
            params={"max": "1"},
        ),
        Probe(
            "GET /webhooks",
            "GET",
            f"{webex}/webhooks",
            "meeting:admin_schedule_read and meeting:admin_participants_read (org webhooks)",
            params={"max": "1"},
        ),
    ]
    if not wxcc_base_url:
        return found
    wxcc = wxcc_base_url.rstrip("/")
    return [
        *found,
        Probe(
            "GET /v2/subscriptions",
            "GET",
            f"{wxcc}/v2/subscriptions",
            f"cjp:config_read (register-webhooks also needs cjp:config_write), and "
            f"{AUTHORISED_HINT} with an administrator role for Contact Center",
        ),
        Probe(
            "POST /v2/tasks (empty body)",
            "POST",
            f"{wxcc}/v2/tasks",
            "cjp:task_write (Create Task v2), and Work Items enabled for the organisation",
            json={},
            ok=(400, 422),
        ),
        Probe(
            "POST /v2/tasks/{nil}/messages",
            "POST",
            f"{wxcc}/v2/tasks/{NIL_TASK_ID}/messages",
            "cjp:task_write (Task Messages v2: marking cancelled work items)",
            json={},
            ok=(400, 404, 422),
        ),
        Probe(
            "POST /v1/tasks/{nil}/end",
            "POST",
            f"{wxcc}/v1/tasks/{NIL_TASK_ID}/end",
            "cjp:user (End Task, v1 only); without it cancelled work items stay marked cancelled",
            ok=(202, 400, 404),
            required=False,
        ),
        Probe(
            "GET /v1/subscriptions/event-types",
            "GET",
            f"{wxcc}/v1/subscriptions/event-types",
            "cjp:config_read",
            required=False,
        ),
        Probe(
            "GET /v1/tasks",
            "GET",
            f"{wxcc}/v1/tasks",
            "cjp:config_read (task search)",
            params={"channelTypes": "workitem", "pageSize": "1"},
            last_hour=True,
            required=False,
        ),
    ]


async def run_checks(
    http: httpx.AsyncClient, tokens: WebexTokens, checks: list[Probe], clock: Clock
) -> list[CheckResult]:
    """Refresh the token, then run every probe; stop after a refused token refresh."""
    try:
        await tokens.access_token()
    except IntegrationError as error:
        hint = (
            "the refresh token was refused: generate new tokens in the developer portal and "
            "set WEBEX_REFRESH_TOKEN (runbook, Service App)"
            if error.code == "unauthorized"
            else f"token endpoint {error.code}: check WEBEX_API_BASE_URL and the network"
        )
        return [CheckResult("token refresh", None, False, hint)]
    results = [CheckResult("token refresh", None, True, "ok")]
    for probe in checks:
        results.append(await _run(http, tokens, probe, clock))
    return results


async def _run(
    http: httpx.AsyncClient, tokens: WebexTokens, probe: Probe, clock: Clock
) -> CheckResult:
    """Send one probe (once more with a fresh token after a 401) and classify its status."""
    params = dict(probe.params)
    if probe.last_hour:
        now = clock.now()
        params["from"] = str(int((now - timedelta(hours=1)).timestamp() * 1000))
        params["to"] = str(int(now.timestamp() * 1000))
    status = 0
    for attempt in (0, 1):
        token = await tokens.access_token(refresh=attempt == 1)
        try:
            response = await http.request(
                probe.method,
                probe.url,
                params=params or None,
                json=probe.json,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            hint = f"unreachable ({type(error).__name__})"
            return CheckResult(probe.name, None, False, hint, probe.required)
        status = response.status_code
        if status != 401:
            break
    return CheckResult(probe.name, status, status in probe.ok, _hint(probe, status), probe.required)


def _hint(probe: Probe, status: int) -> str:
    """Return what a status most likely means for ``probe``."""
    if status in probe.ok:
        return "ok"
    if status == 401:
        return f"token refused: generate new tokens after adding {probe.scope_hint}"
    if status == 403:
        return f"forbidden: needs {probe.scope_hint}"
    if status == 404:
        return "not found: check the API host (WXCC_API_BASE_URL is per region)"
    if status == 429 or status >= 500:
        return "busy or failing: try again later"
    return f"unexpected status for this probe (expected {', '.join(map(str, probe.ok))})"


def passed(results: list[CheckResult]) -> bool:
    """Return whether every required probe succeeded (secondary v1 probes may fail)."""
    return all(r.ok or not r.required for r in results)


def format_report(results: list[CheckResult]) -> str:
    """Return one line per result: OK/FAIL/WARN, name, status and hint."""
    width = max(len(r.name) for r in results)
    lines = [f"{_verdict(r)}  {r.name:<{width}}  {r.status or '-':>3}  {r.hint}" for r in results]
    return "".join(f"{line}\n" for line in lines)


def _verdict(result: CheckResult) -> str:
    """Return ``OK``, ``FAIL`` (required) or ``WARN`` (secondary), padded to four characters."""
    if result.ok:
        return "OK  "
    return "FAIL" if result.required else "WARN"
