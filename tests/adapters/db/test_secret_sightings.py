"""The first sighting of an installation webhook secret, as a fingerprint (ADR 0124 point 8)."""

import json

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.reports import ReportStore
from app.adapters.db.secret_sightings import NAME_PREFIX, StoredSightings
from app.domain.webhook_secrets import Sighting, fingerprint
from tests.support.domain import NOW

SIGHTING = Sighting(fingerprint(b"k" * 32, "the-secret"), NOW)


async def test_a_sighting_is_stored_per_platform_and_replaced(engine: AsyncEngine) -> None:
    store = StoredSightings(engine)

    assert await store.get("wxcc") is None
    await store.put("wxcc", SIGHTING)
    await store.put("webex", Sighting("f" * 64, NOW))
    later = Sighting("a" * 64, NOW.replace(hour=12))
    await store.put("wxcc", later)

    assert await store.get("wxcc") == later
    assert await store.get("webex") == Sighting("f" * 64, NOW)


async def test_the_row_holds_the_fingerprint_and_the_time_and_no_secret(
    engine: AsyncEngine,
) -> None:
    await StoredSightings(engine).put("wxcc", SIGHTING)

    report = await ReportStore(engine).get(f"{NAME_PREFIX}wxcc")

    assert report is not None
    assert set(report.body) == {"fingerprint", "since"}
    assert "the-secret" not in json.dumps(report.body)


async def test_a_row_that_cannot_be_read_is_no_sighting(engine: AsyncEngine) -> None:
    reports = ReportStore(engine)
    store = StoredSightings(engine)

    await reports.put(f"{NAME_PREFIX}a", {"fingerprint": 1, "since": "x"}, NOW)
    await reports.put(f"{NAME_PREFIX}b", {"fingerprint": "f", "since": "not a time"}, NOW)
    await reports.put(f"{NAME_PREFIX}c", {}, NOW)

    assert [await store.get(p) for p in "abc"] == [None, None, None]
