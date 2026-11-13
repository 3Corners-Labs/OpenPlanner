"""The inbound webhook gate (ADR 0124): refusals answer empty and leave a trace with a reason."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog.testing
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.authorization import Scope
from app.main import create_app
from app.web.protection import limiter
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import NOW
from tests.support.webhooks import SECRET, sign, timestamp, wxcc_body

JSON = "application/json"


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "webhook-gate")


def _configured(settings: Settings, database_url: str, **update: object) -> Settings:
    return settings.model_copy(
        update={
            "database_url": database_url,
            "webex_webhook_secret": SecretStr(SECRET),
            "wxcc_webhook_secret": SecretStr(SECRET),
            **update,
        }
    )


@pytest.fixture
async def client(settings: Settings, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(_configured(settings, database_url), FixedClock(NOW)) as client:
        yield client


async def _refusals(database_url: str) -> list[tuple[str, str]]:
    """Return the ``(reason, first detail key)`` of every refusal event, sorted."""
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        entries = await tx.list_audit_events(Scope(everything=True))
    await engine.dispose()
    return sorted(
        (e.event.reason, e.event.detail.split(",")[0])
        for e in entries
        if e.event.action == "webhook.rejected"
    )


def _wxcc_headers(body: bytes) -> dict[str, str]:
    return {"x-webexcc-signature": sign(body), "x-webexcc-timestamp": timestamp(body)}


async def test_a_wrong_content_type_is_a_415_before_the_body_is_read(
    client: httpx.AsyncClient, database_url: str
) -> None:
    body = wxcc_body("task:connected", at=NOW)

    for content_type in ("text/plain", "application/x-www-form-urlencoded", "application/xml"):
        response = await client.post(
            "/webhooks/wxcc",
            content=body,
            headers=_wxcc_headers(body) | {"content-type": content_type},
        )
        assert (response.status_code, response.content) == (415, b""), content_type
    for content_type in (f"{JSON}; charset=utf-8", "application/cloudevents+json"):
        accepted = await client.post(
            "/webhooks/wxcc",
            content=body,
            headers=_wxcc_headers(body) | {"content-type": content_type},
        )
        assert accepted.status_code == 204, content_type

    assert await _refusals(database_url) == [("content_type", "source:wxcc")]


async def test_a_call_without_a_content_type_passes_and_the_signature_decides(
    client: httpx.AsyncClient,
) -> None:
    """The video platform's real deliveries were never observed: a missing header must not break."""
    body = b"{}"
    wxcc = wxcc_body("task:connected", at=NOW)

    signed = await client.post(
        "/webhooks/webex", content=body, headers={"x-spark-signature": sign(body, "sha1")}
    )
    signed_cc = await client.post("/webhooks/wxcc", content=wxcc, headers=_wxcc_headers(wxcc))
    forged = await client.post(
        "/webhooks/webex", content=body, headers={"x-spark-signature": "bad"}
    )

    assert (signed.status_code, signed_cc.status_code, forged.status_code) == (204, 204, 401)


async def test_an_oversized_declared_length_is_refused_without_reading(
    client: httpx.AsyncClient, database_url: str
) -> None:
    response = await client.post("/webhooks/webex", content=b"x" * (65 * 1024))

    assert (response.status_code, response.content) == (413, b"")
    assert await _refusals(database_url) == [("too_large", "source:webex")]


async def test_a_body_sent_slowly_without_a_length_is_cut_off_at_the_limit(
    client: httpx.AsyncClient, database_url: str
) -> None:
    """ADR 0124 point 6: a chunked body is capped while it is read, not after."""
    sent = 0

    async def trickle() -> AsyncIterator[bytes]:
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b"x" * 16 * 1024

    response = await client.post("/webhooks/webex", content=trickle())

    assert (response.status_code, response.content) == (413, b"")
    assert sent < 10  # the limit is passed after 5 chunks; the rest is never read
    assert await _refusals(database_url) == [("too_large", "source:webex")]


async def test_a_body_exactly_at_the_limit_is_read(client: httpx.AsyncClient) -> None:
    body = b"[" + b" " * (64 * 1024 - 2) + b"]"
    assert len(body) == 64 * 1024

    response = await client.post(
        "/webhooks/webex", content=body, headers={"x-spark-signature": sign(body, "sha1")}
    )

    assert response.status_code == 400  # verified, read in full, and not an object


async def test_every_refusal_leaves_its_reason_and_a_flood_is_one_event_each(
    client: httpx.AsyncClient, database_url: str
) -> None:
    """ADR 0124 point 10: reason and platform, deduplicated per window and caller."""
    stale = wxcc_body("task:connected", at=NOW.replace(hour=7))
    not_an_object = b"[1]"
    for _ in range(20):
        await client.post("/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"})
        await client.post("/webhooks/webex", content=b"{}", headers={"content-type": "text/plain"})
        await client.post("/webhooks/webex", content=b"x" * (65 * 1024))
        await client.post(
            "/webhooks/webex",
            content=not_an_object,
            headers={"x-spark-signature": sign(not_an_object, "sha1")},
        )
        await client.post("/webhooks/wxcc", content=stale, headers=_wxcc_headers(stale))
        await client.post("/webhooks/contact-center/genesys", content=b"{}")
        await client.post("/webhooks/contact-center/webex_cc/nobody", content=b"{}")

    assert await _refusals(database_url) == [
        ("content_type", "source:webex"),
        ("invalid_signature", "source:webex"),
        ("malformed", "source:webex"),
        ("stale", "source:wxcc"),
        ("too_large", "source:webex"),
        ("unknown_address", "source:unknown"),
        ("unknown_address", "source:wxcc"),
    ]


async def test_an_address_without_a_secret_leaves_a_trace_too(
    settings: Settings, database_url: str
) -> None:
    async with app_client(settings.model_copy(update={"database_url": database_url})) as client:
        for path in ("/webhooks/wxcc", "/webhooks/contact-center/webex_cc", "/webhooks/webex"):
            response = await client.post(path, content=b"{}")
            assert (response.status_code, response.content) == (404, b""), path

    assert await _refusals(database_url) == [
        ("unknown_address", "source:webex"),
        ("unknown_address", "source:wxcc"),
    ]


async def test_a_refusal_event_holds_nothing_from_the_request(
    client: httpx.AsyncClient, database_url: str
) -> None:
    looks_like_a_secret = "0123456789abcdef" * 4  # gitleaks:allow (test value)
    await client.post(
        "/webhooks/webex",
        content=b'{"email": "ada@example.org"}',
        headers={"x-spark-signature": looks_like_a_secret, "user-agent": "ada-agent"},
    )

    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        entries = await tx.list_audit_events(Scope(everything=True))
    await engine.dispose()
    assert entries
    text = " ".join(f"{e.event.reason} {e.event.detail} {e.event.actor}" for e in entries)
    for forbidden in (looks_like_a_secret, "ada", "127.0.0.1", "testserver", SECRET):
        assert forbidden not in text


@pytest.fixture
async def limited_client(settings: Settings, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    limited = _configured(
        settings, database_url, rate_limit_enabled=True, rate_limit_webhook_per_minute=60
    )
    async with app_client(limited, FixedClock(NOW)) as client:
        yield client


async def test_the_webhook_limit_is_a_setting_with_a_machine_answer(
    limited_client: httpx.AsyncClient, database_url: str
) -> None:
    """ADR 0124 point 5: 429 with ``Retry-After``, an empty body and the reason ``rate``."""
    for _ in range(60):
        refused = await limited_client.post(
            "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"}
        )
        assert refused.status_code == 401

    over = await limited_client.post(
        "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"}
    )
    other_platform = await limited_client.post(
        "/webhooks/wxcc", content=b"{}", headers={"x-webexcc-signature": "bad"}
    )

    assert (over.status_code, over.content) == (429, b"")
    assert over.headers["retry-after"] == "60"
    assert "html" not in over.headers.get("content-type", "")
    assert other_platform.status_code == 401  # its own bucket
    assert ("rate", "source:webex") in await _refusals(database_url)


def _unknown(index: int) -> str:
    return f"/webhooks/contact-center/webex_cc/made-up-{index}"


async def test_probing_made_up_addresses_is_a_404_up_to_the_limit_then_a_429(
    limited_client: httpx.AsyncClient, database_url: str
) -> None:
    """ADR 0124 point 5: the bucket decides between 404 and 429 for addresses that do not exist."""
    for index in range(60):
        response = await limited_client.post(_unknown(index), content=b"{}")
        assert response.status_code == 404

    over = await limited_client.post(_unknown(60), content=b"{}")
    again = await limited_client.post("/webhooks/contact-center/genesys", content=b"{}")

    assert (over.status_code, over.content, over.headers["retry-after"]) == (429, b"", "60")
    assert again.status_code == 429
    assert ("rate", "source:unknown") in await _refusals(database_url)


async def test_rotating_addresses_within_an_ipv6_64_never_opens_a_fresh_bucket(
    settings: Settings, database_url: str
) -> None:
    """Abuse case ADR 0124 step 2a: an IPv6 caller counts by its /64, in every webhook bucket."""
    limited = _configured(
        settings, database_url, rate_limit_enabled=True, rate_limit_webhook_per_minute=5
    )
    app = create_app(limited, FixedClock(NOW))

    async def post(address: str, path: str, **headers: str) -> int:
        transport = httpx.ASGITransport(app=app, client=(address, 4711))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return (await c.post(path, content=b"{}", headers=headers)).status_code

    forged = {"x-spark-signature": "bad"}
    async with app.router.lifespan_context(app):
        # Calls to addresses that do not exist: one bucket per /64, each call from a new address.
        probes = [await post(f"2001:db8:1:2::{n:x}", _unknown(n)) for n in range(1, 7)]
        other_64 = await post("2001:db8:1:3::1", _unknown(7))
        # Refused calls to an address that exists: the same.
        refused = [
            await post(f"2001:db8:5:6::{n:x}", "/webhooks/webex", **forged) for n in range(6)
        ]
        # An IPv4 caller is counted as it is; the mapped form shares its bucket.
        v4 = [await post("203.0.113.7", "/webhooks/webex", **forged) for _ in range(3)]
        mapped = [await post("::ffff:203.0.113.7", "/webhooks/webex", **forged) for _ in range(3)]

    assert probes == [404] * 5 + [429]
    assert other_64 == 404
    assert refused == [401] * 5 + [429]
    assert v4 == [401] * 3
    assert mapped == [401] * 2 + [429]


async def test_a_full_bucket_for_unknown_addresses_never_locks_a_real_address_out(
    limited_client: httpx.AsyncClient,
) -> None:
    """A customer of the same supplier fills the bucket for unknown addresses from the shared
    address to lock real organisations out: it must not work."""
    for index in range(61):
        await limited_client.post(_unknown(index), content=b"{}")
    assert (await limited_client.post(_unknown(99), content=b"{}")).status_code == 429

    body = b"{}"
    signed = await limited_client.post(
        "/webhooks/webex", content=body, headers={"x-spark-signature": sign(body, "sha1")}
    )

    assert signed.status_code == 204


async def test_made_up_slugs_never_open_a_bucket_of_their_own(
    limited_client: httpx.AsyncClient,
) -> None:
    for index in range(20):
        await limited_client.post(_unknown(index), content=b"{}")

    keys = [str(key) for key in limiter.limiter.storage.storage]  # type: ignore[attr-defined]
    assert [k for k in keys if "made-up" in k] == []
    assert len([k for k in keys if "webhook-unknown" in k]) == 1


async def test_a_flood_writes_no_log_line_per_call_but_a_forged_signature_still_does(
    limited_client: httpx.AsyncClient,
) -> None:
    """The security event is the trace of a rate or unknown-address flood, not a line per call."""
    with structlog.testing.capture_logs() as forged:
        await limited_client.post(
            "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "x"}
        )
    with structlog.testing.capture_logs() as flood:
        for index in range(62):
            await limited_client.post(_unknown(index), content=b"{}")

    assert [e for e in flood if e["event"] == "webhook.rejected"] == []
    assert [e["reason"] for e in forged if e["event"] == "webhook.rejected"] == [
        "invalid_signature"
    ]


async def test_forged_calls_never_use_up_the_budget_of_the_authentic_ones(
    limited_client: httpx.AsyncClient,
) -> None:
    """THE abuse case (ADR 0124 point 5): a customer of the same supplier fills the bucket of
    refused calls from the shared address; a correctly signed call is still accepted."""
    for _ in range(60):
        forged = await limited_client.post(
            "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"}
        )
        assert forged.status_code == 401
    over = await limited_client.post(
        "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"}
    )
    assert (over.status_code, over.content) == (429, b"")

    body = b"{}"
    headers = {"x-spark-signature": sign(body, "sha1")}
    wxcc = wxcc_body("task:connected", at=NOW)
    signed = await limited_client.post("/webhooks/webex", content=body, headers=headers)
    signed_cc = await limited_client.post(
        "/webhooks/wxcc", content=wxcc, headers=_wxcc_headers(wxcc)
    )

    assert (signed.status_code, signed_cc.status_code) == (204, 204)


async def test_every_kind_of_refused_call_counts_against_the_refused_bucket(
    limited_client: httpx.AsyncClient,
) -> None:
    """Content type, size, signature and shape share one bucket; over it the answer is 429."""
    not_an_object = b"[1]"
    refused = (
        {"content": b"{}", "headers": {"content-type": "text/plain"}},
        {"content": b"x" * (65 * 1024), "headers": {}},
        {"content": b"{}", "headers": {"x-spark-signature": "bad"}},
        {"content": not_an_object, "headers": {"x-spark-signature": sign(not_an_object, "sha1")}},
    )
    statuses = [
        (await limited_client.post("/webhooks/webex", **call)).status_code for call in refused * 15
    ]

    assert statuses[:4] == [415, 413, 401, 400]
    assert statuses[60:] == []  # sixty refusals in all
    over = await limited_client.post(
        "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "x"}
    )
    assert over.status_code == 429


async def test_verified_calls_beyond_the_limit_are_a_429(limited_client: httpx.AsyncClient) -> None:
    """The supplier itself sending too much: the bucket of verified calls (installation address)."""
    body = b"{}"
    headers = {"x-spark-signature": sign(body, "sha1")}
    for _ in range(60):
        assert (
            await limited_client.post("/webhooks/webex", content=body, headers=headers)
        ).status_code == 204

    over = await limited_client.post("/webhooks/webex", content=body, headers=headers)
    wxcc = wxcc_body("task:connected", at=NOW)
    other = await limited_client.post("/webhooks/wxcc", content=wxcc, headers=_wxcc_headers(wxcc))

    assert (over.status_code, over.content, over.headers["retry-after"]) == (429, b"", "60")
    assert other.status_code == 204  # its own platform, its own bucket


async def test_a_forwarded_header_cannot_change_the_caller_the_buckets_see(
    limited_client: httpx.AsyncClient,
) -> None:
    for index in range(60):
        forged = await limited_client.post(
            "/webhooks/webex",
            content=b"{}",
            headers={"x-spark-signature": "bad", "x-forwarded-for": f"198.51.100.{index}"},
        )
        assert forged.status_code == 401

    over = await limited_client.post(
        "/webhooks/webex",
        content=b"{}",
        headers={
            "x-spark-signature": "bad",
            "x-forwarded-for": "203.0.113.99",
            "forwarded": "for=203.0.113.98",
        },
    )
    keys = [str(key) for key in limiter.limiter.storage.storage]  # type: ignore[attr-defined]

    assert over.status_code == 429  # still the one bucket of the server-derived address
    assert [k for k in keys if "198.51.100" in k or "203.0.113" in k] == []


# --- Source networks (ADR 0124 points 3 and 4, step 2b) -------------------------------------

SUPPLIER = "8.8.8.8"
"""A caller inside the supplier's range (a global address: documentation ranges are not)."""
STRANGER = "8.8.4.4"
SUPPLIER_RANGE = "8.8.8.0/24"


async def _from(
    app: Any, address: str, path: str, body: bytes = b"{}", **headers: str
) -> httpx.Response:
    """Post one call that arrives from ``address``, as the server would derive it."""
    transport = httpx.ASGITransport(app=app, client=(address, 4711))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.post(path, content=body, headers=headers)


def _webex(body: bytes = b"{}") -> dict[str, str]:
    return {"x-spark-signature": sign(body, "sha1")}


@asynccontextmanager
async def _in_app(settings: Settings, database_url: str, **update: object) -> AsyncIterator[Any]:
    """Run the application with ``update`` on top of the test settings."""
    app = create_app(_configured(settings, database_url, **update), FixedClock(NOW))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def listed(settings: Settings, database_url: str) -> AsyncIterator[Any]:
    """The application with a list for the video platform and one for the contact center."""
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_video=SUPPLIER_RANGE,
        webhook_source_networks_contact_center="8.8.9.0/24, 2001:4860:4860::/48",
    ) as app:
        yield app


async def test_a_call_from_inside_the_list_passes_and_one_from_outside_is_a_403(
    listed: Any, database_url: str
) -> None:
    app = listed
    wxcc = wxcc_body("task:connected", at=NOW)

    inside = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())
    outside = await _from(app, STRANGER, "/webhooks/webex", **_webex())
    cc_inside = await _from(
        app, "2001:4860:4860:1::5", "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc)
    )
    cc_outside = await _from(app, SUPPLIER, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc))
    neutral = await _from(
        app, STRANGER, "/webhooks/contact-center/webex_cc", wxcc, **_wxcc_headers(wxcc)
    )

    assert [r.status_code for r in (inside, cc_inside)] == [204, 204]
    assert [(r.status_code, r.content) for r in (outside, cc_outside, neutral)] == [(403, b"")] * 3
    assert await _refusals(database_url) == [
        ("network", "source:webex"),
        ("network", "source:wxcc"),
        ("network", "source:wxcc"),
    ]


async def test_a_refused_network_leaves_no_address_in_its_event(
    listed: Any, database_url: str
) -> None:
    await _from(listed, STRANGER, "/webhooks/webex", **_webex())

    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        entries = await tx.list_audit_events(Scope(everything=True))
    await engine.dispose()
    text = " ".join(f"{e.event.reason} {e.event.detail} {e.event.actor}" for e in entries)
    assert "network" in text
    for forbidden in (STRANGER, "8.8.4", SUPPLIER_RANGE, "8.8.8"):
        assert forbidden not in text


async def test_a_co_tenant_inside_the_suppliers_range_still_fails_the_signature(
    listed: Any, database_url: str
) -> None:
    """The list is defence in depth: the supplier's addresses are shared, the signature decides."""
    forged = await _from(listed, SUPPLIER, "/webhooks/webex", **{"x-spark-signature": "bad"})
    stale = wxcc_body("task:connected", at=NOW.replace(hour=7))
    old = await _from(
        listed, "2001:4860:4860:1::5", "/webhooks/wxcc", stale, **_wxcc_headers(stale)
    )

    assert (forged.status_code, forged.content) == (401, b"")
    assert old.status_code == 401
    assert await _refusals(database_url) == [
        ("invalid_signature", "source:webex"),
        ("stale", "source:wxcc"),
    ]


async def test_without_a_list_any_source_passes_unless_the_host_requires_one(
    settings: Settings, database_url: str
) -> None:
    async with _in_app(settings, database_url) as app:
        anywhere = await _from(app, STRANGER, "/webhooks/webex", **_webex())
    async with _in_app(settings, database_url, webhook_source_networks_required=True) as app:
        required = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())
        wxcc = wxcc_body("task:connected", at=NOW)
        required_cc = await _from(app, SUPPLIER, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc))
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_required=True,
        webhook_source_networks_video=SUPPLIER_RANGE,
    ) as app:
        listed_ok = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())
        cc_still_missing = await _from(app, SUPPLIER, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc))

    assert anywhere.status_code == 204
    assert [(r.status_code, r.content) for r in (required, required_cc)] == [(403, b"")] * 2
    assert (listed_ok.status_code, cc_still_missing.status_code) == (204, 403)
    assert (
        await _refusals(database_url)
        == [("network", "source:webex")] + [("network", "source:wxcc")] * 2
    )


async def test_the_two_refusals_for_the_source_cannot_be_told_apart_by_the_caller(
    settings: Settings, database_url: str
) -> None:
    async with _in_app(settings, database_url, webhook_source_networks_video=SUPPLIER_RANGE) as app:
        outside = await _from(app, STRANGER, "/webhooks/webex", **_webex())
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_video=SUPPLIER_RANGE,
        forwarded_allow_ips="*",
    ) as app:
        unknown = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())

    assert (outside.status_code, outside.content, dict(outside.headers)) == (
        unknown.status_code,
        unknown.content,
        dict(unknown.headers),
    )
    assert await _refusals(database_url) == [
        ("network", "source:webex"),
        ("source_unknown", "source:webex"),
    ]


async def test_a_forged_forwarded_address_does_not_open_a_list_the_proxy_trust_cannot_hold(
    settings: Settings, database_url: str
) -> None:
    """Abuse case: a stranger names a supplier's address in a forwarded header.

    With the proxy trusted too widely (``*``), the server could have taken that address as the
    caller's, so a list is refused as unevaluable. With a narrow trust the gate itself reads no
    header: a ``Forwarded`` header changes nothing and the stranger's own address is checked; an
    ``X-Forwarded-For`` from a peer that is not trusted makes that call, and only that call,
    ``source_unknown``. The stranger is never let in.
    """
    forged = {"x-forwarded-for": SUPPLIER, "forwarded": f"for={SUPPLIER}"} | _webex()
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_video=SUPPLIER_RANGE,
        forwarded_allow_ips="*",
    ) as app:
        wide = await _from(app, STRANGER, "/webhooks/webex", **forged)
        wide_real = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_video=SUPPLIER_RANGE,
        forwarded_allow_ips="172.31.240.2",
    ) as app:
        header_ignored = await _from(
            app, STRANGER, "/webhooks/webex", **{"forwarded": f"for={SUPPLIER}"} | _webex()
        )
        ignored_header = await _from(app, STRANGER, "/webhooks/webex", **forged)
        supplier_after = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())

    assert [r.status_code for r in (wide, wide_real, header_ignored)] == [403, 403, 403]
    assert (ignored_header.status_code, supplier_after.status_code) == (403, 204)
    assert (
        await _refusals(database_url)
        == [("network", "source:webex")] + [("source_unknown", "source:webex")] * 3
    )


async def test_a_list_that_cannot_be_evaluated_is_refused_not_passed(
    settings: Settings, database_url: str
) -> None:
    """Each way the caller's address cannot be trusted: the call is refused with source_unknown,
    also when the caller would be inside the list; an address without a list is not affected."""
    listed = {"webhook_source_networks_video": "10.0.0.0/8, " + SUPPLIER_RANGE}
    public = {"public_base_url": "https://planner.voorbeeld.nl"}
    outcomes: dict[str, tuple[int, int]] = {}

    async def attempt(name: str, caller: str, **update: object) -> None:
        async with _in_app(settings, database_url, **public, **update) as app:
            with_list = await _from(app, caller, "/webhooks/webex", **_webex())
            wxcc = wxcc_body("task:connected", at=NOW)
            without = await _from(app, caller, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc))
        outcomes[name] = (with_list.status_code, without.status_code)

    await attempt("sound", SUPPLIER, **listed, forwarded_allow_ips="172.31.240.2")
    await attempt("wide", SUPPLIER, **listed, forwarded_allow_ips="172.31.0.0/16")
    await attempt("proxy itself", "10.1.2.3", **listed, forwarded_allow_ips="10.1.2.3")
    await attempt("private hop", "10.1.2.3", **listed, forwarded_allow_ips="172.31.240.2")
    await attempt("loopback", "127.0.0.1", **listed)
    await attempt("unreadable trust", SUPPLIER, **listed, forwarded_allow_ips="/run/proxy.sock")

    assert outcomes == {
        "sound": (204, 204),
        "wide": (403, 204),
        "proxy itself": (403, 204),
        "private hop": (403, 204),
        "loopback": (403, 204),
        "unreadable trust": (403, 204),
    }
    assert {r for r, _ in await _refusals(database_url)} == {"source_unknown"}


async def test_a_stranger_forging_forwarded_headers_once_does_not_switch_the_lists_off(
    settings: Settings, database_url: str
) -> None:
    """Abuse case: one request from an untrusted peer with ``X-Forwarded-For`` leaves the proxy
    watch with a sighting, but the sighting decides nothing: a correctly signed call from an allowed
    network is still accepted, and only the stranger's own call is refused."""
    async with _in_app(
        settings,
        database_url,
        webhook_source_networks_video=SUPPLIER_RANGE,
        forwarded_allow_ips="172.31.240.2",
    ) as app:
        forged = await _from(app, STRANGER, "/webhooks/webex", **{"x-forwarded-for": SUPPLIER})
        sighted = app.state.proxy_watch.sighting()
        accepted = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())

    assert sighted is not None
    assert (forged.status_code, accepted.status_code) == (403, 204)
    assert await _refusals(database_url) == [("source_unknown", "source:webex")]


@pytest.mark.parametrize(
    ("headers", "with_list", "without_list"),
    [
        ({}, 204, 204),
        ({"x-forwarded-for": "198.51.100.9"}, 403, 204),  # its peer is not a trusted proxy
        ({"x-forwarded-proto": "https"}, 403, 204),
        ({"x-forwarded-for": SUPPLIER}, 204, 204),  # honoured: the server used it
        ({"forwarded": "for=198.51.100.9"}, 204, 204),  # never used by the server, so not judged
    ],
)
async def test_a_call_whose_own_forwarded_headers_were_ignored_is_source_unknown_only_with_a_list(
    settings: Settings,
    database_url: str,
    headers: dict[str, str],
    with_list: int,
    without_list: int,
) -> None:
    update = {"forwarded_allow_ips": "172.31.240.2"}
    wxcc = wxcc_body("task:connected", at=NOW)
    async with _in_app(
        settings, database_url, webhook_source_networks_video=SUPPLIER_RANGE, **update
    ) as app:
        listed_call = await _from(app, SUPPLIER, "/webhooks/webex", **_webex() | headers)
        open_call = await _from(
            app, SUPPLIER, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc) | headers
        )

    assert (listed_call.status_code, open_call.status_code) == (with_list, without_list)
    expected = [("source_unknown", "source:webex")] if with_list == 403 else []
    assert await _refusals(database_url) == expected


async def test_source_refusals_count_against_the_refused_bucket_never_the_verified_one(
    settings: Settings, database_url: str
) -> None:
    async with _in_app(
        settings,
        database_url,
        rate_limit_enabled=True,
        rate_limit_webhook_per_minute=60,
        webhook_source_networks_video=SUPPLIER_RANGE,
    ) as app:
        refused = [
            (await _from(app, STRANGER, "/webhooks/webex", **_webex())).status_code
            for _ in range(60)
        ]
        over = await _from(app, STRANGER, "/webhooks/webex", **_webex())
        supplier = [
            (await _from(app, SUPPLIER, "/webhooks/webex", **_webex())).status_code
            for _ in range(3)
        ]
        keys = [str(key) for key in limiter.limiter.storage.storage]  # type: ignore[attr-defined]

    assert refused == [403] * 60
    assert (over.status_code, over.content, over.headers["retry-after"]) == (429, b"", "60")
    assert supplier == [204] * 3
    assert len([k for k in keys if "webhook-refused" in k]) == 1
    assert not [k for k in keys if STRANGER in k and "refused" not in k]  # nothing verified
    assert ("rate", "source:webex") in await _refusals(database_url)


@pytest.mark.parametrize(
    ("update", "caller"),
    [
        ({"webhook_source_networks_video": SUPPLIER_RANGE}, STRANGER),  # outside the list
        (
            {"webhook_source_networks_video": SUPPLIER_RANGE, "forwarded_allow_ips": "*"},
            SUPPLIER,  # the address cannot be trusted
        ),
    ],
)
async def test_a_flood_from_outside_the_list_writes_no_log_line_per_call_only_its_event(
    settings: Settings,
    database_url: str,
    update: dict[str, object],
    caller: str,
) -> None:
    """Passers-by cause these refusals, so like ``rate`` they are an event, not a line per call."""
    async with _in_app(settings, database_url, **update) as app:
        with structlog.testing.capture_logs() as flood:
            for _ in range(30):
                refused = await _from(app, caller, "/webhooks/webex", **_webex())
                assert refused.status_code == 403

    assert [e for e in flood if e["event"] == "webhook.rejected"] == []
    assert len(await _refusals(database_url)) == 1  # one event for the caller, held back


@pytest.mark.parametrize(
    "key", ["webhook_source_networks_video", "webhook_source_networks_contact_center"]
)
async def test_an_installation_default_that_cannot_be_read_refuses_every_call_never_passes_it(
    settings: Settings, database_url: str, key: str
) -> None:
    """Abuse case: a corrupted default is no empty list ("any source")."""
    wxcc = wxcc_body("task:connected", at=NOW)
    async with _in_app(settings, database_url, **{key: "not-a-network"}) as app:
        video = await _from(app, SUPPLIER, "/webhooks/webex", **_webex())
        cc = await _from(app, SUPPLIER, "/webhooks/wxcc", wxcc, **_wxcc_headers(wxcc))

    expected = (403, 204) if key.endswith("video") else (204, 403)
    assert (video.status_code, cc.status_code) == expected
