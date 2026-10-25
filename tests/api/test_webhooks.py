"""Webhook endpoints: verified before parsed, unknown events acknowledged, events recorded."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.booking import MeetingRef
from app.domain.calls import CallState, TaskState
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.adapters.test_webhooks import SECRET, sign, timestamp, wxcc_body
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import NOW, TEST_TOKEN_KEY, book


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "webhooks")


@pytest.fixture
async def booking_id(database_url: str) -> int:
    engine = create_engine(database_url)
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://site.webex.com/m/1"))
        await tx.save_task_id(booking_id, "task-1")
    await engine.dispose()
    return booking_id


@pytest.fixture
async def client(settings: Settings, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    configured = settings.model_copy(
        update={
            "database_url": database_url,
            "webex_webhook_secret": SecretStr(SECRET),
            "wxcc_webhook_secret": SecretStr(SECRET),
        }
    )
    async with app_client(configured, FixedClock(NOW)) as client:
        yield client


async def _status(database_url: str, booking_id: int) -> tuple[CallState, TaskState, int]:
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        status = await tx.get_call_status(booking_id)
        events = await tx.get_booking_events(booking_id)
    await engine.dispose()
    assert status is not None
    return status.call, status.task, len(events)


async def test_wxcc_event_is_recorded(
    client: httpx.AsyncClient, booking_id: int, database_url: str
) -> None:
    body = wxcc_body("task:connected", at=NOW)

    response = await client.post(
        "/webhooks/wxcc",
        content=body,
        headers={"x-webexcc-signature": sign(body), "x-webexcc-timestamp": timestamp(body)},
    )

    assert response.status_code == 204
    assert (await _status(database_url, booking_id))[1] is TaskState.CONNECTED


async def test_webex_event_is_recorded(
    client: httpx.AsyncClient, booking_id: int, database_url: str
) -> None:
    body = json.dumps(
        {
            "resource": "meetings",
            "event": "started",
            "data": {"id": "meeting-1_I_1", "meetingSeriesId": "meeting-1"},
        }
    ).encode()

    response = await client.post(
        "/webhooks/webex", content=body, headers={"x-spark-signature": sign(body, "sha1")}
    )

    assert response.status_code == 204
    assert (await _status(database_url, booking_id))[0] is CallState.IN_PROGRESS


async def test_forged_or_stale_webhooks_are_refused(
    client: httpx.AsyncClient, booking_id: int
) -> None:
    body = wxcc_body("task:connected", at=NOW)
    stale = wxcc_body("task:connected", at=NOW.replace(hour=7))

    forged = await client.post(
        "/webhooks/wxcc",
        content=body,
        headers={"x-webexcc-signature": "0" * 64, "x-webexcc-timestamp": timestamp(body)},
    )
    replayed = await client.post(
        "/webhooks/wxcc",
        content=stale,
        headers={"x-webexcc-signature": sign(stale), "x-webexcc-timestamp": timestamp(stale)},
    )
    webex = await client.post(
        "/webhooks/webex", content=b"{}", headers={"x-spark-signature": "bad"}
    )

    assert (forged.status_code, replayed.status_code, webex.status_code) == (401, 401, 401)


async def test_unknown_tasks_are_acknowledged_and_ignored(
    client: httpx.AsyncClient, booking_id: int, database_url: str
) -> None:
    body = wxcc_body("task:ended", at=NOW)
    body = body.replace(b'"task-1"', b'"someone-else"')

    response = await client.post(
        "/webhooks/wxcc",
        content=body,
        headers={"x-webexcc-signature": sign(body), "x-webexcc-timestamp": timestamp(body)},
    )

    assert response.status_code == 204


async def test_oversized_bodies_are_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/webhooks/webex", content=b"x" * (65 * 1024))

    assert response.status_code == 413


async def test_without_a_secret_the_endpoints_do_not_exist(
    settings: Settings, database_url: str
) -> None:
    async with app_client(settings.model_copy(update={"database_url": database_url})) as client:
        assert (await client.post("/webhooks/wxcc", content=b"{}")).status_code == 404
        assert (await client.post("/webhooks/webex", content=b"{}")).status_code == 404


async def test_signed_but_malformed_bodies_are_a_400(client: httpx.AsyncClient) -> None:
    from datetime import UTC, datetime

    for body in (b"not json", b"[1, 2]"):
        webex = await client.post(
            "/webhooks/webex", content=body, headers={"x-spark-signature": sign(body, "sha1")}
        )
        assert webex.status_code == 400
    listing = json.dumps([{"comciscotimestamp": 1}]).encode()
    wxcc = await client.post(
        "/webhooks/wxcc",
        content=listing,
        headers={
            "x-webexcc-signature": sign(listing),
            "x-webexcc-timestamp": str(int(datetime.now(UTC).timestamp() * 1000)),
        },
    )
    assert wxcc.status_code == 401  # not an object: never authentic
