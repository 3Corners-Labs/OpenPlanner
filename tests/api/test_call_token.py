"""The call page's guest token endpoint (ADR 0123 step 4a) and its abuse cases.

Every case of the threat model's flow section has a test here: no session, another booking's
session, outside the join window, a cancelled booking, the switch off, many tokens for one
booking and from one address, the token or a link in a log or a stored field, a cross-site
post, and the host's link in the answer.
"""

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.booking import MeetingRef
from app.domain.calls import EventKind
from app.domain.guest_access import guest_subject
from app.domain.maintenance import MaintenanceReason
from app.domain.ports import IntegrationError
from app.domain.settings_registry import BY_KEY
from app.keys import guest_pseudonym_key
from app.logging import set_line_tap
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import GUEST_EXPIRES, FakeDispatcher, FakeVideo, webex_cc
from tests.support.call_page import embedded_app_client
from tests.support.domain import NOW
from tests.support.participant import (
    FRIDAY_0900,
    FRIDAY_0930,
    SAME_ORIGIN,
    book,
    confirm_newest,
    open_link,
)

START = datetime.fromisoformat(FRIDAY_0900.replace("Z", "+00:00"))
LINK = "https://site.webex.com/site/j.php?MTID=m-guest"
HOST_LINK = "https://site.webex.com/site/j.php?MTID=m-host-secret"
HOST = "host@example.nl"
CROSS_SITE = {"origin": "https://elsewhere.example", "sec-fetch-site": "cross-site"}


@dataclass
class Site:
    """The app with a confirmed booking of Ada, a meeting for it and a fake video provider."""

    client: httpx.AsyncClient
    video: FakeVideo
    settings: Settings
    base: str
    booking_id: int
    clock: FixedClock

    @property
    def token_path(self) -> str:
        """The endpoint of the booking."""
        return f"{self.base}/call/token"

    async def post(self, **headers: str) -> httpx.Response:
        """Ask for a token as the call page's script does, from the page itself."""
        return await self.client.post(self.token_path, headers=headers or SAME_ORIGIN)

    def stranger(self) -> httpx.AsyncClient:
        """A browser without the participant's cookie."""
        return httpx.AsyncClient(
            transport=self.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        )


async def _prepare(settings: Settings) -> None:
    """Let the demo group use the video provider."""
    engine = create_engine(settings.database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, video_host_email=HOST))
    await engine.dispose()


async def _meeting(settings: Settings, booking_id: int) -> None:
    """Give the booking its meeting (the guest link) and a host link that must never leave."""
    engine = create_engine(settings.database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("meeting-1", LINK))
    async with engine.begin() as conn:
        await conn.execute(
            update(BookingRow).where(BookingRow.id == booking_id).values(host_join_url=HOST_LINK)
        )
    await engine.dispose()


async def _newest_booking(client: httpx.AsyncClient) -> int:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.connect() as conn:
        return int(
            (await conn.execute(select(BookingRow.id).order_by(BookingRow.id.desc()))).first()[0]
        )  # type: ignore[index]


@contextmanager
def _log_lines() -> Iterator[list[str]]:
    """Collect every log line the process writes while the block runs."""
    lines: list[str] = []
    set_line_tap(lines.append)
    try:
        yield lines
    finally:
        set_line_tap(None)


def _on(settings: Settings, **more: object) -> Settings:
    """The demo installation with the call page switched on."""
    return settings.model_copy(update={"embedded_call_enabled": True, "log_level": "DEBUG", **more})


async def _site(settings: Settings, at: datetime, video: FakeVideo) -> AsyncIterator[Site]:
    """Book and confirm while it is early, then let the clock say ``at``."""
    await _prepare(settings)
    clock = FixedClock(NOW)
    async with embedded_app_client(
        settings, clock, Integrations(None, video, webex_cc(FakeDispatcher()))
    ) as client:
        assert (await book(client)).status_code == 303
        token = await confirm_newest(client)
        booking_id = await _newest_booking(client)
        await _meeting(settings, booking_id)
        clock.advance(at - NOW)
        base = await open_link(client, token)  # the session starts when the participant arrives
        yield Site(client, video, settings, base, booking_id, clock)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    """An open booking: the clock is five minutes before the start."""
    async for running in _site(_on(demo_settings), START - timedelta(minutes=5), FakeVideo()):
        yield running


# --- what is handed out -------------------------------------------------------------------


async def test_the_answer_is_the_token_when_it_expires_and_the_guest_link(site: Site) -> None:
    response = await site.post()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "token": "guest-token-1",
        "expires_at": GUEST_EXPIRES.isoformat(),
        "link": LINK,
    }


async def test_the_host_link_never_leaves_the_server(site: Site) -> None:
    response = await site.post()

    assert HOST_LINK not in response.text
    assert "m-host-secret" not in response.text
    assert all(HOST_LINK not in value for value in response.headers.values())


async def test_the_platform_gets_a_pseudonymous_subject_and_the_first_name_only(site: Site) -> None:
    await site.post()

    key = guest_pseudonym_key(site.settings)
    assert site.video.guests == [(guest_subject(site.booking_id, key), "Ada")]
    subject, name = site.video.guests[0]
    assert subject.startswith("op1-")
    assert subject != f"op1-{site.booking_id}"  # a keyed digest, not the booking's id
    assert "Lovelace" not in subject + name
    assert "ada@example.org" not in subject + name


async def test_a_reload_asks_for_the_same_guest(site: Site) -> None:
    await site.post()
    await site.post()

    assert len({subject for subject, _ in site.video.guests}) == 1


@pytest.mark.parametrize(("lang", "word"), [("nl", "Deelnemer"), ("en", "Participant")])
async def test_an_organisation_may_name_the_guest_by_its_word_instead(
    demo_settings: Settings, lang: str, word: str
) -> None:
    video = FakeVideo()
    settings = _on(demo_settings, embedded_call_display_name="term")
    async for running in _site(settings, START - timedelta(minutes=5), video):
        response = await running.client.post(
            f"{running.token_path}?lang={lang}", headers=SAME_ORIGIN
        )

        assert response.status_code == 200
        assert [name for _, name in video.guests] == [word]


async def test_the_booking_remembers_that_a_guest_was_minted_once(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def minted() -> datetime | None:
        async with app.state.engine.connect() as conn:
            return (
                await conn.execute(
                    select(BookingRow.guest_minted_at).where(BookingRow.id == site.booking_id)
                )
            ).scalar_one()

    assert await minted() is None
    await site.post()
    first = await minted()
    assert first == START - timedelta(minutes=5)


# --- who may ask --------------------------------------------------------------------------


async def test_without_the_session_the_answer_is_the_one_for_a_booking_that_does_not_exist(
    site: Site,
) -> None:
    async with site.stranger() as stranger:
        refused = await stranger.post(site.token_path, headers=SAME_ORIGIN)
        unknown = await stranger.post("/b/m/no-such-ref/call/token", headers=SAME_ORIGIN)

    assert refused.status_code == unknown.status_code == 403
    assert refused.text == unknown.text
    assert site.video.guests == []


async def test_another_bookings_session_gets_nothing(site: Site) -> None:
    async with site.stranger() as other:
        now = site.clock.now()
        site.clock.advance(NOW + timedelta(hours=12) - now)  # early enough to book the next slot
        assert (await book(other, FRIDAY_0930)).status_code == 303
        theirs = await open_link(other, await confirm_newest(other))
        site.clock.advance(now - site.clock.now())
        assert theirs != site.base

        refused = await other.post(site.token_path, headers=SAME_ORIGIN)
        unknown = await other.post("/b/m/no-such-ref/call/token", headers=SAME_ORIGIN)

    assert refused.status_code == unknown.status_code == 403
    assert refused.text == unknown.text
    assert site.video.guests == []


async def test_a_cross_site_post_is_refused_before_anything_happens(site: Site) -> None:
    refused = await site.post(**CROSS_SITE)
    forged = await site.post(origin="https://elsewhere.example")

    assert refused.status_code == forged.status_code == 403
    assert "guest-token" not in refused.text + forged.text
    assert site.video.guests == []


async def test_the_endpoint_does_not_answer_a_get(site: Site) -> None:
    assert (await site.client.get(site.token_path)).status_code == 405
    assert site.video.guests == []


async def test_it_does_not_exist_while_the_switch_is_off(demo_settings: Settings) -> None:
    video = FakeVideo()
    async for running in _site(
        demo_settings.model_copy(update={"embedded_call_enabled": False}),
        START - timedelta(minutes=5),
        video,
    ):
        off = await running.post()
        nothing = await running.client.post(f"{running.base}/no-such-page", headers=SAME_ORIGIN)

        assert off.status_code == nothing.status_code == 404
        assert off.text == nothing.text
        assert video.guests == []


# --- when ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now",
    [
        START - timedelta(minutes=16),
        START + timedelta(minutes=30) + timedelta(minutes=31),
    ],
    ids=["before the window opens", "after the window closed"],
)
async def test_outside_the_join_window_there_is_no_token(
    demo_settings: Settings, now: datetime
) -> None:
    video = FakeVideo()
    async for running in _site(_on(demo_settings), now, video):
        response = await running.post()

        assert response.status_code == 409
        assert response.json() == {"error": "not_open"}
        assert response.headers["cache-control"] == "no-store"
        assert video.guests == []


@pytest.mark.parametrize(
    "now",
    [
        START - timedelta(minutes=15),
        START + timedelta(minutes=30) + timedelta(minutes=30),
    ],
    ids=["the moment it opens", "the moment it closes"],
)
async def test_the_window_is_the_one_of_the_join_page(
    demo_settings: Settings, now: datetime
) -> None:
    video = FakeVideo()
    async for running in _site(_on(demo_settings), now, video):
        assert (await running.post()).status_code == 200


async def test_a_cancelled_booking_gets_no_token(site: Site) -> None:
    cancelled = await site.client.post(f"{site.base}/cancel", headers=SAME_ORIGIN)
    assert cancelled.status_code == 303

    response = await site.post()

    assert response.status_code == 409
    assert response.json() == {"error": "not_open"}
    assert site.video.guests == []


async def test_a_booking_without_a_meeting_gets_no_token(demo_settings: Settings) -> None:
    video = FakeVideo()
    settings = _on(demo_settings)
    await _prepare(settings)
    clock = FixedClock(NOW)
    async with embedded_app_client(
        settings, clock, Integrations(None, video, webex_cc(FakeDispatcher()))
    ) as client:
        await book(client)
        base = await open_link(client, await confirm_newest(client))
        clock.advance(START - timedelta(minutes=5) - NOW)

        response = await client.post(f"{base}/call/token", headers=SAME_ORIGIN)

    assert response.status_code == 409
    assert video.guests == []


# --- the platform fails -------------------------------------------------------------------


async def test_a_provider_failure_is_a_plain_not_available_now(site: Site) -> None:
    site.video.guest_error = IntegrationError("rejected_403", retryable=False, reason="Ada was")

    response = await site.post()

    assert response.status_code == 503
    assert response.json() == {"error": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "rejected_403" not in response.text  # the page is told no more than "not available"


async def test_a_provider_failure_leaves_the_usual_trace_without_the_token(site: Site) -> None:
    site.video.guest_error = IntegrationError("rejected_403", retryable=False, reason="Ada was")
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    await site.post()

    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.get_booking_events(site.booking_id)
    failed = [e for e in events if e.kind is EventKind.INTEGRATION_ERROR]
    assert [(e.detail, e.message) for e in failed] == [("rejected_403", "")]


async def test_without_a_video_provider_it_is_the_same_plain_answer(
    demo_settings: Settings,
) -> None:
    settings = _on(demo_settings)
    await _prepare(settings)
    clock = FixedClock(NOW)
    async with embedded_app_client(settings, clock) as client:
        await book(client)
        base = await open_link(client, await confirm_newest(client))
        await _meeting(settings, await _newest_booking(client))
        clock.advance(START - timedelta(minutes=5) - NOW)

        response = await client.post(f"{base}/call/token", headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert response.json() == {"error": "unavailable"}


# --- the limits ---------------------------------------------------------------------------


async def test_one_booking_may_ask_a_limited_number_of_times_an_hour(
    demo_settings: Settings,
) -> None:
    video = FakeVideo()
    settings = _on(
        demo_settings,
        rate_limit_enabled=True,
        embedded_call_token_per_booking_per_hour=5,
        rate_limit_reads="600/minute",
    )
    async for running in _site(settings, START - timedelta(minutes=5), video):
        answers = [(await running.post()).status_code for _ in range(7)]

        assert answers == [200] * 5 + [429, 429]
        assert len(video.guests) == 5
        refused = await running.post()
        assert refused.headers["retry-after"] == "60"
        assert refused.headers["cache-control"] == "no-store"
        assert refused.json() == {"error": "rate_limited"}


async def test_a_caller_without_the_session_cannot_use_a_bookings_budget_up(
    demo_settings: Settings,
) -> None:
    video = FakeVideo()
    settings = _on(
        demo_settings,
        rate_limit_enabled=True,
        embedded_call_token_per_booking_per_hour=5,
        rate_limit_reads="600/minute",
        rate_limit_unknown_links="100/hour",
    )
    async for running in _site(settings, START - timedelta(minutes=5), video):
        async with running.stranger() as stranger:
            for _ in range(10):
                await stranger.post(running.token_path, headers=SAME_ORIGIN)

        assert (await running.post()).status_code == 200


async def test_one_address_is_held_by_the_common_read_limit(demo_settings: Settings) -> None:
    video = FakeVideo()
    settings = _on(demo_settings, rate_limit_enabled=True, rate_limit_reads="3/minute")
    async for running in _site(settings, START - timedelta(minutes=5), video):
        answers = [(await running.post()).status_code for _ in range(5)]

        assert answers[:3] == [200, 200, 200]
        assert answers[3:] == [429, 429]
        limited = await running.post()
        assert limited.json() == {"error": "rate_limited"}  # a script's route answers JSON too
        assert limited.headers["cache-control"] == "no-store"
        assert limited.headers["retry-after"] == "60"


# --- what is kept -------------------------------------------------------------------------


def _dump(settings: Settings) -> str:
    """Every table of the database as text, for a search."""
    path = Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))
    with closing(sqlite3.connect(path)) as database:
        return "\n".join(database.iterdump())


async def test_the_token_is_in_no_log_line_and_in_no_stored_field(
    demo_settings: Settings,
) -> None:
    video = FakeVideo()
    settings = _on(demo_settings)
    with _log_lines() as lines:
        async for running in _site(settings, START - timedelta(minutes=5), video):
            ok = await running.post()
            token = ok.json()["token"]
            video.guest_error = IntegrationError("server_error", retryable=True)
            await running.post()  # a failure leaves its own trace
            video.guest_error = None
            await running.client.get(f"{running.base}/call")

    stored = _dump(settings)
    logged = "\n".join(lines)
    assert token == "guest-token-1"
    assert token not in stored
    assert token not in logged
    # The link is the booking's own stored link (purged with it); it is in no log line.
    assert "m-guest" not in logged
    assert "m-host-secret" not in logged
    assert LINK not in logged


async def test_the_token_and_the_links_are_in_no_event_audit_row_or_job(
    demo_settings: Settings,
) -> None:
    video = FakeVideo()
    settings = _on(demo_settings)
    async for running in _site(settings, START - timedelta(minutes=5), video):
        await running.post()
        video.guest_error = IntegrationError("rejected_403", retryable=False)
        await running.post()

    path = Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))
    with closing(sqlite3.connect(path)) as database:
        for table in ("booking_events", "audit_events", "jobs"):
            rows = "\n".join(str(r) for r in database.execute(f"SELECT * FROM {table}"))  # noqa: S608
            for secret in ("guest-token", "m-guest", "m-host-secret", "op1-"):
                assert secret not in rows, (table, secret)


async def test_the_answer_is_never_cacheable_and_never_in_an_address(site: Site) -> None:
    ok = await site.post()
    refused = await site.post(**CROSS_SITE)

    assert ok.headers["cache-control"] == "no-store"
    assert "guest-token" not in str(ok.request.url)
    assert "guest-token" not in refused.text


async def test_attempts_outside_the_window_do_not_use_the_bookings_budget_up(
    demo_settings: Settings,
) -> None:
    video = FakeVideo()
    settings = _on(
        demo_settings,
        rate_limit_enabled=True,
        embedded_call_token_per_booking_per_hour=5,
        rate_limit_reads="600/minute",
    )
    async for running in _site(settings, START - timedelta(minutes=30), video):
        early = [(await running.post()).status_code for _ in range(8)]
        running.clock.advance(timedelta(minutes=25))  # the window opens
        answers = [(await running.post()).status_code for _ in range(6)]

        assert early == [409] * 8
        assert answers == [200] * 5 + [429]


async def test_the_default_budget_of_a_booking_is_a_handful_an_hour(
    demo_settings: Settings,
) -> None:
    spec = BY_KEY["embedded_call_token_per_booking_per_hour"]

    assert demo_settings.embedded_call_token_per_booking_per_hour == 10
    assert (spec.default, spec.minimum, spec.maximum) == (10, 5, 30)


async def test_the_way_into_a_call_stays_open_during_maintenance(demo_settings: Settings) -> None:
    video = FakeVideo()
    async for running in _site(_on(demo_settings), START - timedelta(minutes=5), video):
        app = running.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        await app.state.maintenance_service.switch_on_host("host-cli", MaintenanceReason.UPDATE)
        closed = await running.client.post(f"{running.base}/cancel", headers=SAME_ORIGIN)

        response = await running.post()

        assert closed.status_code == 503  # the installation is in maintenance
        assert response.status_code == 200
        assert len(video.guests) == 1
