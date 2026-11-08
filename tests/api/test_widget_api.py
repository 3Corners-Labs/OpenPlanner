"""Widget API (ADR 0035 §2–4) against a migrated database with the demo group.

Calendar as in test_booking_pages: "now" is Thursday 1 January 2026 08:00 UTC; the demo group
is open on weekdays 09:00-12:00 and 13:00-17:00 Amsterdam time, two places per slot. The demo
group is switched on for embedding and ``SITE`` is on the demo organisation's list.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import (
    AuditEventRow,
    BookingRow,
    OrganisationRow,
    OrganisationTextRow,
)
from app.api.widget_schemas import MAX_BODY_BYTES
from app.config import Settings
from app.domain.maintenance import MaintenanceReason
from app.domain.maintenance_service import MaintenanceService
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import FRIDAY_0900
from tests.support.widget import ADA, BASE, SITE, embed

EVIL = "https://evil.example"


async def _bookings(settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session:
        count = len((await session.scalars(select(BookingRow.id))).all())
    await engine.dispose()
    return count


async def _book(
    site: httpx.AsyncClient, origin: str | None = SITE, **body: object
) -> httpx.Response:
    headers = {"origin": origin} if origin else {}
    return await site.post(f"{BASE}/bookings", json={**ADA, **body}, headers=headers)


async def test_the_group_comes_with_its_form_and_texts_for_an_allowed_origin(
    site: httpx.AsyncClient,
) -> None:
    response = await site.get(f"{BASE}/?lang=en", headers={"origin": SITE})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["vary"] == "Origin"
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["name"], body["locale"], body["closure"]) == ("Demo: videogesprek", "en", None)
    assert body["page_url"].endswith("/g/demo")
    assert [f["name"] for f in body["fields"]] == [
        "first_name",
        "last_name",
        "email",
        "phone",
        "q_onderwerp",  # the demo's example fields (ADR 0055)
        "q_tolk",
    ]
    assert body["honeypot"] == "website"
    assert body["texts"]["form.submit"] == "Book appointment"
    assert "Demo: videogesprek" in body["texts"]["widget.more"]


async def test_the_month_and_the_first_free_day_match_the_standalone_page(
    site: httpx.AsyncClient,
) -> None:
    body = (await site.get(f"{BASE}/availability", headers={"origin": SITE})).json()

    assert (body["month"]["month"], body["month"]["heading"]) == ("2026-01", "januari 2026")
    assert (body["month"]["previous"], body["month"]["next"]) == (None, "2026-02")
    first_week = body["month"]["weeks"][0]
    assert first_week[:3] == [None, None, None]  # 1 January 2026 is a Thursday
    assert first_week[3]["date"] == "2026-01-01"
    assert first_week[3]["free"]
    assert first_week[3]["today"]
    # Today 09:00 local + 60 minutes notice: the first bookable slot today is 10:00.
    assert body["first"] == {"start": "2026-01-01T09:00:00Z", "label": "donderdag 1 januari, 10:00"}
    assert body["times"]["date"] == "2026-01-01"
    assert body["times"]["groups"][0]["times"][0]["start"] == "2026-01-01T09:00:00Z"


async def test_another_day_and_month_can_be_asked_for(site: httpx.AsyncClient) -> None:
    day = (await site.get(f"{BASE}/availability?day=2026-01-02")).json()
    month = (await site.get(f"{BASE}/availability?month=2026-02")).json()
    junk = await site.get(f"{BASE}/availability?day=morgen&month=%00")

    assert day["times"]["date"] == "2026-01-02"
    assert day["times"]["groups"][0]["times"][0]["label"] == "09:00"
    assert month["month"]["month"] == "2026-02"
    assert junk.status_code == 200  # ignored, as on the standalone page


async def test_a_booking_from_an_allowed_origin_returns_no_token_and_no_personal_data(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await _book(site, phone="")

    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == SITE
    body = response.json()
    assert body["status"] == "booked"
    assert (body["start"], body["end"]) == (FRIDAY_0900, "2026-01-02T08:30:00Z")
    assert body["label"] == "vrijdag 2 januari 2026, 09:00–09:30"
    assert "e-mail" in body["message"]
    text = response.text
    assert "Ada" not in text
    assert "ada@example.org" not in text
    assert "/b/" not in text
    assert "token" not in text
    assert await _bookings(demo_settings) == 1


@pytest.mark.parametrize(
    "origin", [None, EVIL, "null", f"{SITE}.evil.example", "http://www.acme.example"]
)
async def test_a_booking_from_any_other_origin_is_refused_and_books_nothing(
    site: httpx.AsyncClient, demo_settings: Settings, origin: str | None
) -> None:
    response = await _book(site, origin=origin)

    assert response.status_code == 403
    assert response.json() == {"error": "origin_not_allowed", "field": None}
    assert "access-control-allow-origin" not in response.headers
    assert await _bookings(demo_settings) == 0


async def test_reads_from_a_foreign_origin_are_refused(site: httpx.AsyncClient) -> None:
    for path in ("/", "/availability"):
        response = await site.get(f"{BASE}{path}", headers={"origin": EVIL})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers


async def test_reads_without_an_origin_get_no_cors_header(site: httpx.AsyncClient) -> None:
    response = await site.get(f"{BASE}/")

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_a_group_that_is_off_or_in_another_organisation_is_not_found(
    demo_settings: Settings,
) -> None:
    await embed(demo_settings, enabled=False)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        off = await client.get(f"{BASE}/", headers={"origin": SITE})
        other = await client.get("/api/v1/widget/andere/demo/", headers={"origin": SITE})
        unknown = await client.get("/api/v1/widget/demo/onbekend/availability")
        booking = await client.post(f"{BASE}/bookings", json=ADA, headers={"origin": SITE})

    for response in (off, other, unknown, booking):
        assert response.status_code == 404
        assert response.json() == {"error": "group_not_found", "field": None}
        assert "access-control-allow-origin" not in response.headers


async def test_the_honeypot_and_malformed_bodies_are_refused(site: httpx.AsyncClient) -> None:
    honeypot = await _book(site, website="http://spam.example")
    extra = await _book(site, ssn="123456789")
    start = await _book(site, start="vrijdag")
    raw = await site.post(f"{BASE}/bookings", content=b"{not json", headers={"origin": SITE})
    large = await site.post(
        f"{BASE}/bookings", content=b" " * (MAX_BODY_BYTES + 1), headers={"origin": SITE}
    )

    assert honeypot.status_code == 400
    assert (extra.status_code, start.status_code, raw.status_code) == (422, 422, 422)
    assert large.status_code == 413
    for response in (honeypot, extra, start, raw, large):
        assert response.json()["error"] == "invalid_request"
        assert response.headers["access-control-allow-origin"] == SITE  # readable errors


async def test_a_bad_email_is_named_without_echoing_it(site: httpx.AsyncClient) -> None:
    response = await _book(site, email="ada-at-example")

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_contact", "field": "email"}
    assert "ada-at-example" not in response.text


async def test_a_taken_slot_is_a_conflict(site: httpx.AsyncClient) -> None:
    await _book(site)
    await _book(site, email="b@example.org")

    third = await _book(site, email="c@example.org")

    assert third.status_code == 409
    assert third.json()["error"] == "slot_full"


async def test_the_preflight_answers_only_allowed_origins(site: httpx.AsyncClient) -> None:
    ask = {
        "access-control-request-method": "POST",
        "access-control-request-headers": "content-type",
    }

    allowed = await site.options(f"{BASE}/bookings", headers={"origin": SITE, **ask})
    foreign = await site.options(f"{BASE}/bookings", headers={"origin": EVIL, **ask})

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == SITE
    assert allowed.headers["access-control-allow-methods"] == "POST"
    assert allowed.headers["access-control-allow-headers"] == "content-type"
    assert "access-control-allow-credentials" not in allowed.headers
    assert foreign.status_code == 403
    assert "access-control-allow-origin" not in foreign.headers


async def test_slot_exhaustion_from_a_host_page_hits_the_same_rate_limit(
    demo_settings: Settings,
) -> None:
    await embed(demo_settings)
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_bookings": "2/hour"}
    )
    async with app_client(limited, FixedClock(NOW)) as client:
        headers = {"origin": SITE}
        for email in ("a@example.org", "b@example.org"):
            await client.post(f"{BASE}/bookings", json={**ADA, "email": email}, headers=headers)
        third = await client.post(
            f"{BASE}/bookings",
            json={**ADA, "start": "2026-01-02T08:30:00Z", "email": "c@example.org"},
            headers=headers,
        )

    assert third.status_code == 429
    assert third.json()["error"] == "rate_limited"
    assert third.headers["retry-after"] == "60"
    assert third.headers["access-control-allow-origin"] == SITE


async def _rate_limit_events(settings: Settings) -> list[tuple[str, str]]:
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session:
        rows = await session.scalars(
            select(AuditEventRow).where(AuditEventRow.action == "request.rate_limited")
        )
        found = [(row.reason, row.detail) for row in rows]
    await engine.dispose()
    return found


async def test_a_rate_limit_hit_is_a_security_event_of_the_widget(
    demo_settings: Settings,
) -> None:
    """ADR 0053: the widget answers its own 429, so it records the hit as the pages do.

    slowapi's own line of the hit is not logged (it carries the address and the path), so
    without the event a flood on the widget would leave no trace at all.
    """
    await embed(demo_settings)
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_reads": "1/minute"}
    )
    async with app_client(limited, FixedClock(NOW)) as client:
        statuses = [
            (await client.get(f"{BASE}/", headers={"origin": SITE})).status_code for _ in range(3)
        ]

    assert statuses == [200, 429, 429]
    ((reason, detail),) = await _rate_limit_events(demo_settings)
    assert reason == "too_many_requests"
    assert detail.startswith("surface:widget,client:")


async def test_the_api_is_in_the_openapi_spec(site: httpx.AsyncClient) -> None:
    spec = (await site.get("/openapi.json")).json()

    assert {
        "/api/v1/widget/{org}/{group}/",
        "/api/v1/widget/{org}/{group}/availability",
        "/api/v1/widget/{org}/{group}/bookings",
    } <= set(spec["paths"])
    booking = spec["paths"]["/api/v1/widget/{org}/{group}/bookings"]["post"]
    assert "application/json" in booking["requestBody"]["content"]
    assert "options" not in spec["paths"]["/api/v1/widget/{org}/{group}/bookings"]


async def test_the_texts_before_the_group_are_public(site: httpx.AsyncClient) -> None:
    response = await site.get("/api/v1/widget/texts?lang=en", headers={"origin": EVIL})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    body = response.json()
    assert body["locale"] == "en"
    assert set(body["texts"]) == {
        "widget.region",
        "widget.loading",
        "widget.unavailable",
        "widget.maintenance",
        "widget.retry",
    }


async def test_during_maintenance_the_group_routes_close_but_texts_and_preflight_stay_open(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    # ADR 0038 with ADR 0035: the widget closes as /g/{slug} does, readable by the host page.
    app = site._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    maintenance: MaintenanceService = app.state.maintenance_service
    await maintenance.switch_on_host("host-cli", MaintenanceReason.OTHER)
    headers = {"origin": SITE}

    closed = [
        await site.get(f"{BASE}/", headers=headers),
        await site.get(f"{BASE}/availability", headers=headers),
        await _book(site),
    ]
    texts = await site.get("/api/v1/widget/texts", headers=headers)
    preflight = await site.options(
        f"{BASE}/bookings",
        headers={**headers, "access-control-request-method": "POST"},
    )
    foreign = await site.get(f"{BASE}/", headers={"origin": EVIL})

    for response in closed:
        assert response.status_code == 503
        assert response.json() == {"error": "maintenance", "field": None}
        assert response.headers["retry-after"] == str(demo_settings.maintenance_retry_after_seconds)
        assert response.headers["access-control-allow-origin"] == SITE
    assert await _bookings(demo_settings) == 0
    assert (texts.status_code, preflight.status_code) == (200, 204)
    assert foreign.status_code == 403  # the origin check comes first


async def test_the_group_introduction_uses_the_organisations_own_text(
    demo_settings: Settings,
) -> None:
    await embed(demo_settings)
    engine = create_engine(demo_settings.database_url)
    async with AsyncSession(engine) as session, session.begin():
        demo = await session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == "demo")
        )
        session.add(
            OrganisationTextRow(
                organisation_id=demo,
                locale="en",
                key="group.intro",
                text="Pick a time ({minutes}).",
            )
        )
    await engine.dispose()

    async with app_client(demo_settings, FixedClock(NOW)) as client:
        body = (await client.get(f"{BASE}/?lang=en", headers={"origin": SITE})).json()

    assert body["intro"] == "Pick a time (30)."


async def test_the_body_budget_fits_the_longest_form_the_schema_accepts(
    site: httpx.AsyncClient,
) -> None:
    # Release audit v0.4.0 P2 (#518): four 1,000-character answers plus contact details are
    # more than the old 4,096 bytes and must not be refused for their size.
    answers = {f"vraag_{n}": "x" * 1000 for n in range(4)}
    fifteen = {f"vraag_{n}": "é" * 1000 for n in range(15)}

    four = await _book(site, answers=answers)
    longest = await _book(site, answers=fifteen)

    assert four.status_code != 413
    assert longest.status_code != 413
    assert MAX_BODY_BYTES > 4096
