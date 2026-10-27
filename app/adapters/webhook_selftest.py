"""``webhooks-health``: send self-signed test events to OpenPlanner's own webhook endpoints.

The events go through ``WEBHOOK_BASE_URL``, the same way Webex and WxCC deliver them, so the
test covers DNS, the tunnel or proxy, the ``web`` container and the secret it verifies with.
Per endpoint two events are sent:

- signed with the secret of this environment: ``204`` proves delivery and verification;
- with a wrong signature: ``401`` proves that unsigned events are refused.

The ids in the events (``openplanner-selftest-<random>``) belong to no meeting and no task:
OpenPlanner acknowledges them as unknown and changes nothing. No bearer token is sent, and the
secrets themselves never leave this process; only the HMAC is in a header.

    POST {base}/webhooks/webex  X-Spark-Signature (HMAC-SHA1)        meetings/ended
    POST {base}/webhooks/wxcc   X-WebexCC-Signature (HMAC-SHA256)    task:ended, +timestamp
"""

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx

from app.adapters.diagnostics import CheckResult
from app.adapters.webhooks import event_body, sign_webex, sign_wxcc

SELFTEST_PREFIX = "openplanner-selftest-"


class Outcome(StrEnum):
    """What a test delivery showed (a code; the text is chosen where it is shown)."""

    DELIVERED = "delivered"
    REFUSED = "refused"
    SIGNATURE_REFUSED = "signature_refused"
    UNSIGNED_ACCEPTED = "unsigned_accepted"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    PROXY_ERROR = "proxy_error"
    UNREACHABLE = "unreachable"
    UNEXPECTED = "unexpected"


HINTS = {
    Outcome.DELIVERED: "delivered and verified (204)",
    Outcome.REFUSED: "an event with a wrong signature is refused (401)",
    Outcome.SIGNATURE_REFUSED: "signature refused: the secret in this environment differs from "
    "the web container's (WEBEX_WEBHOOK_SECRET / WXCC_WEBHOOK_SECRET; for WxCC also the clock)",
    Outcome.UNSIGNED_ACCEPTED: "an event with a wrong signature was accepted: verification is off",
    Outcome.NOT_FOUND: "not found: the web container has no secret for this endpoint, or "
    "WEBHOOK_BASE_URL does not reach OpenPlanner",
    Outcome.RATE_LIMITED: "rate limited: try again in a minute",
    Outcome.PROXY_ERROR: "proxy or tunnel error: is the tunnel running and pointing at web:8000?",
    Outcome.UNREACHABLE: "unreachable: check DNS and the tunnel for WEBHOOK_BASE_URL",
    Outcome.UNEXPECTED: "unexpected status: check what answers on WEBHOOK_BASE_URL",
}
"""Operator text for the command line; the admin portal translates the codes itself."""


@dataclass(frozen=True)
class Delivery:
    """One test delivery: endpoint, signed or not, HTTP status and outcome."""

    endpoint: str
    signed: bool
    status: int | None
    outcome: Outcome
    detail: str = ""
    """The network error's class name for an unreachable endpoint."""

    @property
    def ok(self) -> bool:
        """Whether the endpoint behaved as it should for this event."""
        return self.outcome is (Outcome.DELIVERED if self.signed else Outcome.REFUSED)


async def self_test(
    http: httpx.AsyncClient,
    base_url: str,
    *,
    webex_secret: str | None,
    wxcc_secret: str | None,
    now: datetime,
) -> list[Delivery]:
    """Send the test events for every endpoint whose secret is configured here."""
    base = base_url.rstrip("/")
    results = []
    for signed in (True, False) if webex_secret is not None else ():
        body = event_body({"resource": "meetings", "event": "ended", "data": {"id": _test_id()}})
        key = webex_secret if signed and webex_secret else secrets.token_hex(16)
        headers = sign_webex(body, key)
        results.append(
            await _deliver(http, f"{base}/webhooks/webex", "webex", signed, body, headers)
        )
    for signed in (True, False) if wxcc_secret is not None else ():
        stamp = int(now.timestamp() * 1000)
        data = {"taskId": _test_id()}
        body = event_body({"type": "task:ended", "comciscotimestamp": stamp, "data": data})
        key = wxcc_secret if signed and wxcc_secret else secrets.token_hex(16)
        headers = sign_wxcc(body, key, stamp)
        results.append(await _deliver(http, f"{base}/webhooks/wxcc", "wxcc", signed, body, headers))
    return results


def _test_id() -> str:
    """Return an id that no meeting or task has."""
    return f"{SELFTEST_PREFIX}{secrets.token_hex(8)}"


async def _deliver(
    http: httpx.AsyncClient,
    url: str,
    endpoint: str,
    signed: bool,
    body: bytes,
    headers: dict[str, str],
) -> Delivery:
    """Post one test event and classify the answer."""
    try:
        response = await http.post(
            url,
            content=body,
            headers=headers | {"Content-Type": "application/json"},
            follow_redirects=False,
        )
    except httpx.HTTPError as error:
        return Delivery(endpoint, signed, None, Outcome.UNREACHABLE, type(error).__name__)
    return Delivery(endpoint, signed, response.status_code, _outcome(response.status_code, signed))


def _outcome(status: int, signed: bool) -> Outcome:
    """Return what ``status`` means for a signed or a wrongly signed event."""
    if status == 204:
        return Outcome.DELIVERED if signed else Outcome.UNSIGNED_ACCEPTED
    if status == 401:
        return Outcome.SIGNATURE_REFUSED if signed else Outcome.REFUSED
    if status == 404:
        return Outcome.NOT_FOUND
    if status == 429:
        return Outcome.RATE_LIMITED
    if status >= 500:
        return Outcome.PROXY_ERROR
    return Outcome.UNEXPECTED


def delivery_checks(results: list[Delivery]) -> list[CheckResult]:
    """Return the deliveries as ``wxcc-check``-style result lines."""
    checks = []
    for r in results:
        name = f"POST /webhooks/{r.endpoint} ({'signed' if r.signed else 'wrong signature'})"
        hint = HINTS[r.outcome] + (f" ({r.detail})" if r.detail else "")
        checks.append(CheckResult(name, r.status, r.ok, hint))
    return checks


def deliveries_to_report(results: list[Delivery]) -> dict[str, Any]:
    """Return the deliveries as JSON-safe data for the admin portal."""
    return {"deliveries": [asdict(r) for r in results]}


def deliveries_from_report(report: dict[str, Any]) -> list[Delivery]:
    """Rebuild deliveries stored by ``deliveries_to_report``."""
    return [
        Delivery(**(d | {"outcome": Outcome(d["outcome"])})) for d in report.get("deliveries", [])
    ]
