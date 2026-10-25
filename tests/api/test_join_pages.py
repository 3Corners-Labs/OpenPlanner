"""The citizen's join page (ADR 0017): time window, redirect to Webex, recorded on the timeline."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.booking import MeetingRef
from app.domain.calls import EventKind
from app.domain.tokens import hash_token
from tests.api.test_booking_pages import FRIDAY_0900, book, token_of
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

START = datetime.fromisoformat(FRIDAY_0900.replace("Z", "+00:00"))
LINK = "https://site.webex.com/site/j.php?MTID=m123"


async def _wire(database_url: str, token_booking: int | None = None, link: str = LINK) -> None:
    engine = create_engine(database_url)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, video_host_email="host@example.nl"))
        if token_booking is not None:
            await tx.save_meeting(token_booking, MeetingRef("meeting-1", link))
    await engine.dispose()


async def _events(database_url: str, booking_id: int) -> list[EventKind]:
    engine = create_engine(database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        events = await tx.get_booking_events(booking_id)
    await engine.dispose()
    return [e.kind for e in events]


@pytest.fixture
async def booked(demo_settings: Settings) -> tuple[str, int, Settings]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        response = await book(client)
        token = token_of(response)
    engine = create_engine(demo_settings.database_url)
    async with SqlUnitOfWork(engine).read() as tx:
        booking = await tx.get_booking_by_token_hash(hash_token(token))
    await engine.dispose()
    assert booking is not None and booking.id is not None  # noqa: PT018
    return token, booking.id, demo_settings


async def test_no_join_button_without_video(booked: tuple[str, int, Settings]) -> None:
    token, _, settings = booked
    async with app_client(settings, FixedClock(NOW)) as client:
        manage = (await client.get(f"/b/{token}")).text
        join = (await client.get(f"/b/{token}/join")).text

    assert f'href="/b/{token}/join"' not in manage
    assert "wordt klaargezet" in join  # no meeting yet


async def test_page_waits_until_fifteen_minutes_before(booked: tuple[str, int, Settings]) -> None:
    token, booking_id, settings = booked
    await _wire(settings.database_url, booking_id)

    async with app_client(settings, FixedClock(START - timedelta(minutes=30))) as client:
        early = await client.get(f"/b/{token}/join")
        manage = (await client.get(f"/b/{token}")).text
        go = await client.get(f"/b/{token}/join/go")

    assert "http-equiv" not in early.text  # no automatic reload (WCAG 2.2.1)
    assert f'href="/b/{token}/join">Status verversen' in early.text
    assert "U kunt deelnemen vanaf 08:45" in early.text
    assert f'href="/b/{token}/join"' in manage
    assert go.headers["location"] == f"/b/{token}/join"
    assert await _events(settings.database_url, booking_id) == []


async def test_open_page_sends_the_citizen_to_webex(booked: tuple[str, int, Settings]) -> None:
    token, booking_id, settings = booked
    await _wire(settings.database_url, booking_id)

    async with app_client(settings, FixedClock(START - timedelta(minutes=5))) as client:
        page = await client.get(f"/b/{token}/join")
        go = await client.get(f"/b/{token}/join/go")

    assert f'href="/b/{token}/join/go"' in page.text
    assert "Status verversen" not in page.text
    assert "wachtruimte" in page.text
    assert go.status_code == 303
    assert go.headers["location"] == LINK
    assert await _events(settings.database_url, booking_id) == [
        EventKind.JOIN_PAGE_OPENED,
        EventKind.JOIN_CLICKED,
    ]


async def test_closed_after_the_call(booked: tuple[str, int, Settings]) -> None:
    token, booking_id, settings = booked
    await _wire(settings.database_url, booking_id)

    async with app_client(settings, FixedClock(START + timedelta(hours=2))) as client:
        page = (await client.get(f"/b/{token}/join")).text

    assert "Dit gesprek is voorbij" in page


async def test_never_redirects_to_a_non_https_link(booked: tuple[str, int, Settings]) -> None:
    token, booking_id, settings = booked
    await _wire(settings.database_url, booking_id, link="javascript:alert(1)")

    async with app_client(settings, FixedClock(START)) as client:
        go = await client.get(f"/b/{token}/join/go")

    assert go.headers["location"] == f"/b/{token}/join"


async def test_unknown_token_is_a_404(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        assert (await client.get("/b/not-a-token/join")).status_code == 404
