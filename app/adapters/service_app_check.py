"""The Service App check: ``wxcc-check`` on the command line and the button on *Koppelingen*.

One function runs the probes of ``diagnostics`` with the region check behind them (ADR 0082
point 3), so the portal and the CLI show the same verdicts; the result is stored as the
report ``service_app_check`` for the integrations page. A report holds names, statuses,
verdicts and hints only: never a token, a secret or a response body.
"""

from datetime import datetime
from typing import Any

import httpx

from app.adapters.db.reports import ReportStore
from app.adapters.diagnostics import CheckResult, passed, probes, run_checks
from app.adapters.webex_auth import WebexTokens
from app.adapters.wxcc_config import region_check
from app.domain.ports import Clock

SERVICE_APP_REPORT = "service_app_check"

NOT_CONFIGURED = CheckResult(
    "token refresh",
    None,
    False,
    "Webex is not configured (WEBEX_CLIENT_ID, WEBEX_CLIENT_SECRET, WEBEX_REFRESH_TOKEN)",
)
"""The one line of a check without a Service App."""


async def service_app_check(
    http: httpx.AsyncClient,
    tokens: WebexTokens | None,
    webex_base_url: str,
    wxcc_base_url: str | None,
    clock: Clock,
) -> list[CheckResult]:
    """Run every probe, then the WxCC region check when the token works."""
    if tokens is None:
        return [NOT_CONFIGURED]
    results = await run_checks(http, tokens, probes(webex_base_url, wxcc_base_url), clock)
    if wxcc_base_url and results[0].ok:
        token = await tokens.access_token()
        results.append((await region_check(http, token, wxcc_base_url))[0])
    return results


def report_body(results: list[CheckResult], wxcc_checked: bool) -> dict[str, Any]:
    """Return the report stored for the integrations page."""
    return {
        "passed": passed(results),
        "wxcc": wxcc_checked,
        "results": [
            {
                "name": r.name,
                "status": r.status,
                "ok": r.ok,
                "required": r.required,
                "hint": r.hint,
            }
            for r in results
        ],
    }


def results_from_report(body: dict[str, Any]) -> list[CheckResult]:
    """Read the stored lines back; a malformed line is left out."""
    results = []
    for item in body.get("results", []):
        if not isinstance(item, dict) or "name" not in item:
            continue
        status = item.get("status")
        results.append(
            CheckResult(
                str(item["name"]),
                int(status) if isinstance(status, int) else None,
                bool(item.get("ok")),
                str(item.get("hint", "")),
                bool(item.get("required", True)),
            )
        )
    return results


async def store_service_app_check(
    reports: ReportStore, results: list[CheckResult], wxcc_checked: bool, now: datetime
) -> None:
    """Keep the check's outcome for the integrations page."""
    await reports.put(SERVICE_APP_REPORT, report_body(results, wxcc_checked), now)
