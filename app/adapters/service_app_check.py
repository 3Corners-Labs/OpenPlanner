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
from app.adapters.vendored_files import STATIC_VENDOR, FileTrust, VendoredFile
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

SDK_FILE_CHECK = "Call page SDK file"
"""The name of the line that reports whether the call page may offer its SDK file."""

SDK_HINTS = {
    FileTrust.TRUSTED: "ok",
    FileTrust.NOT_PLACED: (
        "not placed: participants get the 'not available in this page' state after the device "
        "check; place the file (docs/runbook.md, the call page's SDK) or switch "
        "EMBEDDED_CALL_ENABLED off"
    ),
    FileTrust.NO_CHECKSUM: (
        "the manifest has no checksum of the pinned version: the call page does not offer the "
        "file; replace the file and its checksum together (docs/runbook.md)"
    ),
    FileTrust.DIFFERS: (
        "the file differs from the manifest's checksum: the call page does not offer it; "
        "place the file the manifest names again (docs/runbook.md)"
    ),
}
"""What an operator reads for each verdict on the SDK file (no path, no hash)."""


def sdk_file_result(trust: FileTrust) -> CheckResult:
    """Return the check's line for the vendored SDK file's ``trust`` (ADR 0123 point 3)."""
    return CheckResult(
        SDK_FILE_CHECK, None, trust is FileTrust.TRUSTED, SDK_HINTS[trust], code=f"sdk.{trust}"
    )


async def service_app_check(
    http: httpx.AsyncClient,
    tokens: WebexTokens | None,
    webex_base_url: str,
    wxcc_base_url: str | None,
    clock: Clock,
    *,
    embedded_call_enabled: bool = False,
    sdk: VendoredFile | None = None,
) -> list[CheckResult]:
    """Run every probe, then the WxCC region check when the token works.

    With ``embedded_call_enabled`` the guest token probe is added and required (ADR 0123
    point 17), and so is the line for the vendored SDK file: whether the call page may offer it
    (placed, checksum of the pinned version, bytes equal to it; point 3). ``sdk`` is the file
    to judge (the application's own by default). Without the switch nothing is asked of the
    guest endpoint and the file is not judged.
    """
    if tokens is None:
        return [NOT_CONFIGURED, *_sdk_lines(embedded_call_enabled, sdk)]
    results = await run_checks(
        http,
        tokens,
        probes(webex_base_url, wxcc_base_url, guest=embedded_call_enabled),
        clock,
    )
    if wxcc_base_url and results[0].ok:
        token = await tokens.access_token()
        results.append((await region_check(http, token, wxcc_base_url))[0])
    return [*results, *_sdk_lines(embedded_call_enabled, sdk)]


def _sdk_lines(embedded_call_enabled: bool, sdk: VendoredFile | None) -> list[CheckResult]:
    """Return the SDK file's line, or none while the embedded call is off."""
    if not embedded_call_enabled:
        return []
    return [sdk_file_result((sdk or VendoredFile(STATIC_VENDOR / "webex")).trust())]


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
