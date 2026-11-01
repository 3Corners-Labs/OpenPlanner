"""Parameter pollution on the booking form and the widget API (ASVS V1.2.2, V5.4.1).

A field or query parameter sent twice must never let the first, unvalidated copy through: the
request is refused or handled deterministically, and the value that counts is the one that is
validated (the last, as Starlette reads it). Fixtures and helpers come from the widget and
booking page tests.
"""

import json
import re

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow, SlotRow
from app.config import Settings
from tests.api.test_booking_pages import ADA as FORM_ADA
from tests.api.test_booking_pages import FRIDAY_0900, FRIDAY_0930, SAME_ORIGIN
from tests.api.test_widget_api import ADA as WIDGET_ADA
from tests.api.test_widget_api import BASE, SITE
from tests.api.test_widget_api import site as widget_site

site = widget_site  # the app with the demo group embeddable, as the widget tests run it


async def _starts(settings: Settings) -> list[str]:
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session:
        rows = (
            await session.scalars(
                select(SlotRow.start).join(BookingRow, BookingRow.slot_id == SlotRow.id)
            )
        ).all()
    await engine.dispose()
    return sorted(f"{row:%Y-%m-%dT%H:%M}" for row in rows)


async def _post_form(site: httpx.AsyncClient, **fields: object) -> httpx.Response:
    return await site.post(
        "/g/demo/book", data={"start": FRIDAY_0900, **FORM_ADA, **fields}, headers=SAME_ORIGIN
    )


async def test_a_start_sent_twice_books_the_last_one_or_nothing(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    response = await _post_form(site, start=[FRIDAY_0900, FRIDAY_0930])

    assert response.status_code in (303, 400, 422)
    # One booking at the last start (09:30 Amsterdam is 08:30 UTC), or none; never the first.
    assert await _starts(demo_settings) in ([], ["2026-01-02T08:30"])


@pytest.mark.parametrize(
    ("field", "values", "status"),
    [
        ("first_name", ["Ada", ""], 422),
        ("first_name", ["", "Ada"], 303),
        ("email", ["ada@example.org", "not-an-address"], 422),
        ("email", ["not-an-address", "ada@example.org"], 303),
    ],
)
async def test_a_contact_field_sent_twice_is_validated_as_the_last_value(
    site: httpx.AsyncClient, field: str, values: list[str], status: int
) -> None:
    response = await _post_form(site, **{field: values})

    assert response.status_code == status


async def test_the_honeypot_cannot_be_hidden_in_a_second_copy(site: httpx.AsyncClient) -> None:
    first = await _post_form(site, website=["", "spam"])
    second = await _post_form(site, website=["spam", ""])

    assert first.status_code == 400
    assert second.status_code in (303, 400)  # deterministic: the last copy decides


@pytest.mark.parametrize(
    ("path", "once", "twice"),
    [
        ("/g/demo", {"month": "2026-01"}, [("month", "2026-02"), ("month", "2026-01")]),
        ("/g/demo", {"day": "2026-01-02"}, [("day", "2026-01-01"), ("day", "2026-01-02")]),
        ("/g/demo", {"lang": "en"}, [("lang", "nl"), ("lang", "en")]),
        ("/g/demo/book", {"start": FRIDAY_0900}, [("start", "x"), ("start", FRIDAY_0900)]),
    ],
)
async def test_a_query_parameter_sent_twice_gives_the_page_of_the_last_one(
    site: httpx.AsyncClient, path: str, once: dict[str, str], twice: list[tuple[str, str]]
) -> None:
    single = await site.get(path, params=once)
    doubled = await site.get(path, params=twice)

    assert doubled.status_code == single.status_code
    # The language switcher repeats the query in its links; the page itself must be the same.
    assert re.sub(r'href="[^"]*"', "", doubled.text) == re.sub(r'href="[^"]*"', "", single.text)


@pytest.mark.parametrize(
    ("once", "twice"),
    [
        ({"month": "2026-01"}, [("month", "2026-02"), ("month", "2026-01")]),
        ({"day": "2026-01-02"}, [("day", "garbage"), ("day", "2026-01-02")]),
        ({"lang": "en"}, [("lang", "nl"), ("lang", "en")]),
    ],
)
async def test_a_widget_query_parameter_sent_twice_gives_the_answer_of_the_last_one(
    site: httpx.AsyncClient, once: dict[str, str], twice: list[tuple[str, str]]
) -> None:
    headers = {"origin": SITE}
    single = await site.get(f"{BASE}/availability", params=once, headers=headers)
    doubled = await site.get(f"{BASE}/availability", params=twice, headers=headers)

    assert doubled.status_code == single.status_code == 200
    assert doubled.json() == single.json()


async def test_a_json_key_sent_twice_is_validated_as_the_last_value(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    def body(*emails: str) -> bytes:
        fixed = {k: v for k, v in WIDGET_ADA.items() if k != "email"}
        pairs = ",".join(f'"email":{json.dumps(e)}' for e in emails)
        return (json.dumps(fixed)[:-1] + "," + pairs + "}").encode()

    headers = {"origin": SITE, "content-type": "application/json"}
    bad_last = await site.post(
        f"{BASE}/bookings", content=body("ada@example.org", "nope"), headers=headers
    )
    assert (bad_last.status_code, bad_last.json()["error"]) == (422, "invalid_contact")
    assert await _starts(demo_settings) == []

    good_last = await site.post(
        f"{BASE}/bookings", content=body("nope", "ada@example.org"), headers=headers
    )
    assert good_last.status_code == 201
