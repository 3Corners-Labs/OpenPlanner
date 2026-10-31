"""``IntegrationChecker`` on Webex and WxCC: the "Koppeling controleren" button.

For one group it walks the chain a call depends on and turns every link into a ``Finding``:

    webex     a probe meeting is scheduled for the host exactly as a booking would schedule
              one (same adapter, same body: licence, scope, host, time zone and options in one
              go) and deleted at once (ADR 0065); a refusal carries Webex's reason. For a group
              that opens the web app (ADR 0075) the probe's link is visited once, anonymously,
              before it is deleted: Webex must carry ``launchApp=true`` through its redirect.
              Then a second probe with the trial call's options (entry before the host, ADR
              0071): a warning when the site refuses it, and one when it accepts the request
              but stores other values, since the participant would then wait in the lobby for nobody
    wxcc      region ─► channel ─► asset ─► schema ─► entry point ─► flow ─► queue ─► Screen Pop
              (``wxcc_config``, the same rules as ``wxcc-config-check``)
    webhooks  WEBHOOK_BASE_URL is https; this host's Webex webhooks and the WxCC subscription
              exist and are current (``registrations``); a signed test event is delivered and a
              wrongly signed one refused (``webhook_selftest``)

Besides the probe meetings nothing is created or deleted at the platforms, and the self-test
events belong to no booking. A platform that refuses or cannot be reached becomes a finding;
the checker never raises for it. Findings carry names, ids, status codes and Webex's masked
reason only: no secrets, tokens or citizen data.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import httpx

from app.adapters.diagnostics import CheckResult
from app.adapters.registrations import Expected, Listing, list_registrations
from app.adapters.webex_auth import AuthorizedClient, WebexTokens
from app.adapters.webex_meetings import WebexMeetings
from app.adapters.webhook_selftest import self_test
from app.adapters.wxcc_config import GroupRouting, check_group, fetch_config, region_check
from app.domain.calls import BROWSER_LINK_PARAMS, MeetingRequest, join_link
from app.domain.checks import CheckArea, CheckStatus, Finding
from app.domain.ports import Clock, IntegrationError, RequestLog, RequestRecord
from app.domain.scheduling import Group, JoinClient

PROBE_TITLE = "OpenPlanner controle"
"""Title of the probe meeting; the host may see it for a second in the Webex calendar."""
PROBE_AHEAD = timedelta(days=1)
"""The probe is scheduled this far ahead, at the same clock time, so it is never 'now'."""
PROBE_LENGTH = timedelta(minutes=30)
"""Length of the probe meeting: the usual slot."""


@dataclass(frozen=True)
class CheckerConfig:
    """Where the platforms and our webhook endpoints are, and the webhook secrets."""

    webex_base_url: str
    wxcc_base_url: str | None
    webhook_base: str
    webex_secret: str | None
    wxcc_secret: str | None


def _status(result: CheckResult) -> CheckStatus:
    """Map a check line to a status: a failed optional check is a warning."""
    if result.ok:
        return CheckStatus.OK
    return CheckStatus.FAIL if result.required else CheckStatus.WARN


def _value(value: object) -> str:
    """Return ``value`` as Webex writes it in JSON (``true``, ``false``, a word)."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _browser_finding(code: str, params: tuple[tuple[str, str], ...] = ()) -> Finding:
    """Return the finding for the browser link: a warning unless it works."""
    status = CheckStatus.OK if code.endswith("_ok") else CheckStatus.WARN
    return Finding(CheckArea.WEBEX, code, status, params)


def _finding(area: CheckArea, result: CheckResult) -> Finding:
    """Turn a coded check line into a finding of ``area``."""
    return Finding(area, f"{area.value}.{result.code}", _status(result), result.params)


class PlatformChecker:
    """Checks a group against Webex and WxCC with the Service App's tokens.

    Args:
        http: Shared HTTP client.
        tokens: The Service App's tokens.
        config: Platform addresses, webhook base and secrets.
        clock: Current time, for the signed test events.
        request_log: Where the probe meeting's requests are reported (ADR 0068).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: WebexTokens,
        config: CheckerConfig,
        clock: Clock,
        request_log: RequestLog | None = None,
    ) -> None:
        """Store the collaborators."""
        self._http = http
        self._tokens = tokens
        self._client = AuthorizedClient(http, tokens, request_log)
        self._config = config
        self._clock = clock
        self._request_log = request_log

    async def check_group(self, group: Group, peers: Sequence[Group]) -> list[Finding]:
        """Return the findings for ``group``, in the order of the chain."""
        return [
            *await self._webex(group),
            *await self._wxcc(group, peers),
            *await self._webhooks(group),
        ]

    async def _webex(self, group: Group) -> list[Finding]:
        """Schedule a probe meeting for the group's host as a booking would, then delete it."""
        host = group.video_host_email
        if not host:
            return [Finding(CheckArea.WEBEX, "webex.no_host", CheckStatus.WARN)]
        meetings = WebexMeetings(self._client, self._config.webex_base_url, self._clock)
        start = self._clock.now().replace(second=0, microsecond=0) + PROBE_AHEAD
        request = MeetingRequest(PROBE_TITLE, start, start + PROBE_LENGTH, group.timezone, host)
        try:
            probe = await meetings.create_meeting(request)
        except IntegrationError as error:
            if error.code == "rejected_404":
                code = "webex.host_unknown"
            elif error.code in ("rejected_403", "unauthorized"):
                code = "webex.host_refused"
            elif error.code.startswith("rejected_") or error.code == "unexpected_response":
                code = "webex.schedule_refused"
            else:
                code = "webex.unreachable"
            params = (("host", host), ("error", error.code), ("reason", error.reason or "-"))
            return [Finding(CheckArea.WEBEX, code, CheckStatus.FAIL, params)]
        findings = [Finding(CheckArea.WEBEX, "webex.host_ok", CheckStatus.OK, (("host", host),))]
        findings += await self._browser_link(group, probe.link)
        findings += await self._delete_probe(meetings, probe.id, host, "webex.probe_left")
        if group.trial_call_enabled:
            findings += await self._trial_probe(meetings, group, host, start)
        return findings

    async def _trial_probe(
        self, meetings: WebexMeetings, group: Group, host: str, start: datetime
    ) -> list[Finding]:
        """Schedule a trial-call probe, the meeting a citizen's trial call is (ADR 0071).

        Webex may refuse entry before the host on a site; that is a warning, since the booking's
        own meeting works without it.
        """
        request = replace(
            MeetingRequest(PROBE_TITLE, start, start + PROBE_LENGTH, group.timezone, host),
            join_before_host=True,
        )
        try:
            probe = await meetings.probe_meeting(request)
        except IntegrationError as error:
            params = (("host", host), ("error", error.code), ("reason", error.reason or "-"))
            return [Finding(CheckArea.WEBEX, "webex.trial_refused", CheckStatus.WARN, params)]
        if probe.ignored:  # accepted, not kept: the participant waits in the lobby (ADR 0075)
            stored = ", ".join(f"{key}={_value(value)}" for key, value in probe.ignored.items())
            lobby = (("host", host), ("stored", stored))
            outcome = Finding(CheckArea.WEBEX, "webex.trial_lobby", CheckStatus.WARN, lobby)
        else:
            outcome = Finding(CheckArea.WEBEX, "webex.trial_ok", CheckStatus.OK, (("host", host),))
        return [
            outcome,
            *await self._delete_probe(meetings, probe.ref.id, host, "webex.trial_left"),
        ]

    async def _browser_link(self, group: Group, link: str | None) -> list[Finding]:
        """Visit the probe's browser link once and see whether Webex still carries the parameter.

        Only for a group that opens the web app (ADR 0075). The visit is anonymous, follows no
        redirect and sends nothing of a participant: Webex answers the short link with a redirect to
        its join page, and that redirect must still contain ``launchApp=true``. A warning, never
        a failure: without the parameter the participant gets Webex's own choice page.
        """
        if group.join_client is not JoinClient.BROWSER:
            return []
        url = join_link(link, JoinClient.BROWSER)
        if url is None:  # not an https link: never visited
            return [_browser_finding("webex.browser_link_lost", (("status", "-"),))]
        started = time.monotonic()
        try:
            response = await self._http.get(url, follow_redirects=False)
        except httpx.HTTPError as error:
            await self._record(url, 0, "unreachable", started)
            name = type(error).__name__
            return [_browser_finding("webex.browser_link_unreachable", (("error", name),))]
        await self._record(url, response.status_code, "", started)
        kept = set(BROWSER_LINK_PARAMS) <= set(
            parse_qsl(urlsplit(response.headers.get("location", "")).query)
        )
        if 300 <= response.status_code < 400 and kept:
            return [_browser_finding("webex.browser_link_ok")]
        return [
            _browser_finding("webex.browser_link_lost", (("status", str(response.status_code)),))
        ]

    async def _record(self, url: str, status: int, error: str, started: float) -> None:
        """Report the visit to the request log without its query (ADR 0068)."""
        if self._request_log is not None:
            path = urlsplit(url).path
            millis = int((time.monotonic() - started) * 1000)
            await self._request_log.record(RequestRecord("GET", path, status, error, "", millis))

    async def _delete_probe(
        self, meetings: WebexMeetings, meeting_id: str, host: str, code: str
    ) -> list[Finding]:
        """Delete a probe meeting; one that stays is a warning naming it."""
        try:
            await meetings.delete_meeting(meeting_id, host)
        except IntegrationError as error:
            params = (("host", host), ("meeting", meeting_id), ("error", error.code))
            return [Finding(CheckArea.WEBEX, code, CheckStatus.WARN, params)]
        return []

    async def _wxcc(self, group: Group, peers: Sequence[Group]) -> list[Finding]:
        """Walk the WxCC routing chain of the group's work items."""
        base = self._config.wxcc_base_url
        if not base:
            return [Finding(CheckArea.WXCC, "wxcc.not_configured", CheckStatus.SKIPPED)]
        if not group.wxcc_channel or not group.wxcc_business_address:
            return [Finding(CheckArea.WXCC, "wxcc.no_mapping", CheckStatus.WARN)]
        routing = GroupRouting(
            group.slug, group.wxcc_channel, group.wxcc_business_address, group.routing_value
        )
        sharing = sorted(
            p.slug
            for p in [group, *(p for p in peers if p.id != group.id)]
            if p.wxcc_channel and p.wxcc_business_address == group.wxcc_business_address
        )
        try:
            token = await self._tokens.access_token()
            region, host, org = await region_check(self._http, token, base)
            findings = [_finding(CheckArea.WXCC, region)]
            if host and org:
                config = await fetch_config(self._http, token, host, org)
                findings += [
                    _finding(CheckArea.WXCC, r) for r in check_group(routing, config, sharing)
                ]
        except IntegrationError as error:
            return [
                Finding(
                    CheckArea.WXCC,
                    "wxcc.config_refused",
                    CheckStatus.FAIL,
                    (("error", error.code),),
                )
            ]
        return findings

    async def _webhooks(self, group: Group) -> list[Finding]:
        """Check this group's registrations and that signed events reach OpenPlanner."""
        config = self._config
        base = config.webhook_base.rstrip("/")
        if not base.startswith("https://"):
            return [
                Finding(
                    CheckArea.WEBHOOKS, "webhooks.not_https", CheckStatus.FAIL, (("base", base),)
                )
            ]
        host = (group.video_host_email or "").strip().lower()
        wxcc_target = (
            f"{base}/webhooks/wxcc" if config.wxcc_base_url and config.wxcc_secret else None
        )
        expected = Expected(f"{base}/webhooks/webex", wxcc_target, (host,) if host else ())
        listing = await list_registrations(
            self._client, config.webex_base_url, config.wxcc_base_url, expected
        )
        findings = _registration_findings(listing, host, wxcc_target is not None)
        deliveries = await self_test(
            self._http,
            base,
            webex_secret=config.webex_secret,
            wxcc_secret=config.wxcc_secret,
            now=self._clock.now(),
        )
        if not deliveries:
            findings.append(Finding(CheckArea.WEBHOOKS, "webhooks.no_secret", CheckStatus.WARN))
        failed = [d for d in deliveries if not d.ok]
        findings += [
            Finding(
                CheckArea.WEBHOOKS,
                "webhooks.delivery_failed",
                CheckStatus.FAIL,
                (("endpoint", d.endpoint), ("outcome", d.outcome.value)),
            )
            for d in failed
        ]
        if deliveries and not failed:
            findings.append(Finding(CheckArea.WEBHOOKS, "webhooks.delivery_ok", CheckStatus.OK))
        return findings


def _registration_findings(listing: Listing, host: str, wxcc_expected: bool) -> list[Finding]:
    """Turn the registration listing into findings for one host account."""
    area = CheckArea.WEBHOOKS
    findings = [
        Finding(
            area,
            "webhooks.listing_refused",
            CheckStatus.FAIL,
            (("platform", e.platform), ("error", e.code)),
        )
        for e in listing.errors
    ]
    if host and not any(e.platform == "webex" for e in listing.errors):
        missing = [
            m.split()[1] for m in listing.missing if m.startswith("webex") and f"[{host}]" in m
        ]
        if missing:
            findings.append(
                Finding(
                    area,
                    "webhooks.webex_missing",
                    CheckStatus.WARN,
                    (("events", ", ".join(missing)),),
                )
            )
        else:
            findings.append(Finding(area, "webhooks.webex_ok", CheckStatus.OK))
    if wxcc_expected and not any(e.platform == "wxcc" for e in listing.errors):
        if "wxcc task events" in listing.missing:
            findings.append(Finding(area, "webhooks.wxcc_missing", CheckStatus.FAIL))
        else:
            findings.append(Finding(area, "webhooks.wxcc_ok", CheckStatus.OK))
    if listing.stale:
        findings.append(
            Finding(area, "webhooks.stale", CheckStatus.WARN, (("count", str(len(listing.stale))),))
        )
    return findings
