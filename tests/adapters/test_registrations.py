"""Listing, classifying and removing OpenPlanner's webhook registrations (recorded shapes)."""

from typing import Any

import httpx
import pytest

from app.adapters.registrations import (
    Expected,
    Listing,
    ListingError,
    Reason,
    State,
    delete_stale,
    format_listing,
    list_registrations,
    listing_from_report,
    listing_to_report,
)
from tests.fakes.tokens import authorized_client

WEBEX = "https://webexapis.com/v1"
WXCC = "https://api.wxcc-eu2.cisco.com"
BASE = "https://issues-tvs-chronicle-exact.trycloudflare.com"
OLD_TUNNEL = "https://old-quick-tunnel.trycloudflare.com"
HOST = "host-demo@example.nl"
WEBHOOK_SECRET_VALUE = "webhook-secret-never-printed"  # noqa: S105 - test value
FOREIGN_TOKEN = "foreign-token-in-query"  # noqa: S105 - test value

EXPECTED = Expected(
    webex_target=f"{BASE}/webhooks/webex",
    wxcc_target=f"{BASE}/webhooks/wxcc",
    hosts=(HOST,),
)


def _webex(
    hook_id: str,
    resource: str,
    event: str,
    *,
    target: str = f"{BASE}/webhooks/webex",
    host_filter: str | None = f"hostEmail={HOST}",
    status: str = "active",
    name: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": hook_id,
        "name": name or f"OpenPlanner {resource} {event}",
        "targetUrl": target,
        "resource": resource,
        "event": event,
        "secret": WEBHOOK_SECRET_VALUE,
        "status": status,
        "ownedBy": "org",
    }
    if host_filter:
        item["filter"] = host_filter
    return item


CURRENT_WEBEX = [
    _webex("Y2-started", "meetings", "started"),
    _webex("Y2-ended", "meetings", "ended"),
    _webex("Y2-joined", "meetingParticipants", "joined"),
    _webex("Y2-left", "meetingParticipants", "left"),
]

RECORDED_WEBEX = [
    *CURRENT_WEBEX,
    # 2026-10-25: a refused create left two unfiltered webhooks behind.
    _webex("Y2-unfiltered-1", "meetings", "started", host_filter=None),
    _webex("Y2-unfiltered-2", "meetings", "ended", host_filter=None),
    _webex("Y2-old-tunnel", "meetings", "started", target=f"{OLD_TUNNEL}/webhooks/webex"),
    _webex("Y2-old-host", "meetings", "started", host_filter="hostEmail=former@example.nl"),
    _webex("Y2-inactive", "meetingParticipants", "left", status="inactive"),
    _webex("Y2-duplicate", "meetings", "ended"),
    _webex(
        "Y2-foreign",
        "messages",
        "created",
        target=f"https://bot.example.com/hook?token={FOREIGN_TOKEN}",
        name="Someone else's bot",
    ),
]

RECORDED_WXCC = [
    {
        "id": "sub-current",
        "name": "OpenPlanner task events",
        "destinationUrl": f"{BASE}/webhooks/wxcc",
        "eventTypes": ["task:new", "task:connected", "task:ended", "task:failed"],
        "resourceVersion": "task:1.0.0",
        "status": "active",
    },
    {
        "id": "sub-old",
        "name": "OpenPlanner task events",
        "destinationUrl": f"{OLD_TUNNEL}/webhooks/wxcc",
        "eventTypes": ["task:new", "task:connected", "task:ended", "task:failed"],
    },
    {
        "id": "sub-foreign",
        "name": "Reporting",
        "destinationUrl": "https://reports.example.com/in",
        "eventTypes": ["agent:login"],
    },
]


class Recorder:
    """A mock Webex/WxCC that answers with the recorded listings and records every call."""

    def __init__(
        self,
        webex: list[dict[str, Any]] | None = None,
        wxcc: list[dict[str, Any]] | None = None,
        *,
        listing_status: int = 200,
        delete_status: dict[str, int] | None = None,
    ) -> None:
        self.webex = RECORDED_WEBEX if webex is None else webex
        self.wxcc = RECORDED_WXCC if wxcc is None else wxcc
        self.listing_status = listing_status
        self.delete_status = delete_status or {}
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(self.delete_status.get(path.rsplit("/", 1)[-1], 204))
        if path == "/v1/webhooks":
            if self.listing_status != 200:
                return httpx.Response(self.listing_status)
            return httpx.Response(200, json={"items": self.webex})
        if path == "/v2/subscriptions":
            return httpx.Response(200, json={"data": self.wxcc, "meta": {"orgId": "org"}})
        return httpx.Response(404)

    @property
    def deleted(self) -> list[str]:
        return [r.url.path for r in self.seen if r.method == "DELETE"]


async def _list(recorder: Recorder, *, wxcc: str | None = WXCC) -> Listing:
    return await list_registrations(authorized_client(recorder), WEBEX, wxcc, EXPECTED)


async def _reset(recorder: Recorder, *, wxcc: str | None = WXCC, confirmed: bool) -> list[str]:
    client = authorized_client(recorder)
    listing = await list_registrations(client, WEBEX, wxcc, EXPECTED)
    return await delete_stale(client, WEBEX, wxcc, listing, confirmed=confirmed)


async def test_webex_is_always_listed_with_owned_by_org() -> None:
    recorder = Recorder()

    await _list(recorder)

    listing = next(r for r in recorder.seen if r.url.path == "/v1/webhooks")
    assert listing.url.params["ownedBy"] == "org"


async def test_each_registration_is_current_stale_or_foreign_with_a_reason() -> None:
    listing = await _list(Recorder())

    states = {r.id: (r.state, r.reason) for r in listing.registrations}
    assert {states[i.get("id")][0] for i in CURRENT_WEBEX} == {State.CURRENT}
    assert states["Y2-unfiltered-1"] == (State.STALE, Reason.UNFILTERED)
    assert states["Y2-old-tunnel"] == (State.STALE, Reason.OTHER_TARGET)
    assert states["Y2-old-host"] == (State.STALE, Reason.UNKNOWN_HOST)
    assert states["Y2-inactive"] == (State.STALE, Reason.INACTIVE)
    assert states["Y2-duplicate"] == (State.STALE, Reason.DUPLICATE)
    assert states["Y2-foreign"][0] is State.FOREIGN
    assert states["sub-current"] == (State.CURRENT, Reason.NONE)
    assert states["sub-old"] == (State.STALE, Reason.OTHER_TARGET)
    assert states["sub-foreign"][0] is State.FOREIGN
    assert listing.missing == []


async def test_a_registration_the_groups_need_but_nobody_made_is_missing() -> None:
    listing = await _list(Recorder(webex=CURRENT_WEBEX[:2], wxcc=[]))

    assert listing.missing == [
        f"webex meetingParticipants/joined [{HOST}]",
        f"webex meetingParticipants/left [{HOST}]",
        "wxcc task events",
    ]


async def test_the_report_never_shows_secrets_or_foreign_query_strings() -> None:
    listing = await _list(Recorder())

    report = format_listing(listing)

    assert WEBHOOK_SECRET_VALUE not in report
    assert FOREIGN_TOKEN not in report
    assert "https://bot.example.com" in report
    assert f"hostEmail={HOST}" in report
    assert "secret set" in report
    assert "STALE" in report
    assert "no hostEmail filter" in report


async def test_a_refused_webex_listing_is_reported_and_wxcc_still_listed() -> None:
    listing = await _list(Recorder(listing_status=403))

    assert listing.errors == [ListingError("webex", "rejected_403")]
    assert "webex listing: refused (rejected_403)" in format_listing(listing)
    assert {r.platform for r in listing.registrations} == {"wxcc"}
    assert listing.complete is False


async def test_without_a_wxcc_host_only_webex_is_listed() -> None:
    recorder = Recorder()

    listing = await _list(recorder, wxcc=None)

    assert {r.platform for r in listing.registrations} == {"webex"}
    assert all(r.url.host == "webexapis.com" for r in recorder.seen)


@pytest.mark.parametrize(
    "elsewhere", ["https://evil.example.com/v1/webhooks", "http://webexapis.com/v1/webhooks"]
)
async def test_a_next_page_is_followed_on_the_same_https_host_only(elsewhere: str) -> None:
    pages: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        pages.append(request)
        if request.url.params.get("cursor") == "2":
            return httpx.Response(
                200,
                json={"items": CURRENT_WEBEX[2:]},
                headers={"Link": f'<{elsewhere}>; rel="next"'},
            )
        return httpx.Response(
            200,
            json={"items": CURRENT_WEBEX[:2]},
            headers={"Link": f'<{WEBEX}/webhooks?ownedBy=org&cursor=2>; rel="next"'},
        )

    listing = await list_registrations(authorized_client(route), WEBEX, None, EXPECTED)

    assert len(listing.registrations) == 4
    assert [p.url.host for p in pages] == ["webexapis.com", "webexapis.com"]


async def test_a_dry_run_deletes_nothing_and_says_what_it_would_delete() -> None:
    recorder = Recorder()
    lines = await _reset(recorder, confirmed=False)

    assert recorder.deleted == []
    assert len(lines) == 7
    assert all(line.startswith("would delete ") for line in lines)


async def test_delete_removes_only_stale_registrations_and_reports_refusals() -> None:
    recorder = Recorder(delete_status={"Y2-unfiltered-1": 403, "Y2-unfiltered-2": 404})
    lines = await _reset(recorder, confirmed=True)

    assert sorted(recorder.deleted) == sorted(
        [
            "/v1/webhooks/Y2-unfiltered-1",
            "/v1/webhooks/Y2-unfiltered-2",
            "/v1/webhooks/Y2-old-tunnel",
            "/v1/webhooks/Y2-old-host",
            "/v1/webhooks/Y2-inactive",
            "/v1/webhooks/Y2-duplicate",
            "/v2/subscriptions/sub-old",
        ]
    )
    refused = next(line for line in lines if "Y2-unfiltered-1" in line)
    assert "refused (rejected_403)" in refused
    assert "Webex quirk" in refused
    assert any("Y2-unfiltered-2" in line and "already gone" in line for line in lines)
    assert sum(line.startswith("deleted ") for line in lines) == 5


async def test_an_id_that_is_not_an_api_id_is_never_put_in_a_path() -> None:
    recorder = Recorder(webex=[_webex("../../people/me", "meetings", "started", host_filter=None)])
    lines = await _reset(recorder, wxcc=None, confirmed=True)

    assert recorder.deleted == []
    assert lines == ["skipped webex meetings/started: id is not a valid API id"]


@pytest.mark.parametrize("status", [429, 503])
async def test_a_busy_api_during_delete_is_reported_and_the_rest_continues(status: int) -> None:
    recorder = Recorder(delete_status={"Y2-unfiltered-1": status})
    lines = await _reset(recorder, confirmed=True)

    assert any("Y2-unfiltered-1" in line and "failed" in line for line in lines)
    assert len(recorder.deleted) == 7


async def test_the_stored_report_round_trips_without_secrets() -> None:
    listing = await _list(Recorder())

    stored = listing_to_report(listing)

    assert WEBHOOK_SECRET_VALUE not in str(stored)
    assert FOREIGN_TOKEN not in str(stored)
    assert listing_from_report(stored) == listing
