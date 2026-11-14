"""The call page's endpoint for who the participant talks to (ADR 0123 step 5c) and its abuse cases.

The name comes from our own record of who accepted the booking, by the organisation's setting, and
never from the video platform. Every abuse case of the threat model's flow section has a test here:
another booking's session, the switch off, the three settings, no claim, the purge, an erased staff
member, the read limit and the answer that carries a name or nothing and no identifier.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.authorization import Role
from app.domain.booking import MeetingRef
from app.domain.staff import Membership, StaffMember
from app.domain.tokens import agent_pseudonym
from app.keys import agent_pseudonym_key
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import (
    FRIDAY_0900,
    FRIDAY_0930,
    book,
    confirm_newest,
    open_link,
)

START = datetime.fromisoformat(FRIDAY_0900.replace("Z", "+00:00"))
SUBJECT = "staff-sanne"


@dataclass
class Site:
    """The app with a confirmed booking of Ada that a staff member may have accepted."""

    client: httpx.AsyncClient
    settings: Settings
    base: str
    booking_id: int
    clock: FixedClock

    @property
    def path(self) -> str:
        """The endpoint of the booking."""
        return f"{self.base}/call/staff"

    async def ask(self, **headers: str) -> httpx.Response:
        """Ask as the call page's script does."""
        return await self.client.get(self.path, headers=headers)

    def stranger(self) -> httpx.AsyncClient:
        """A browser without the participant's cookie."""
        return httpx.AsyncClient(
            transport=self.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        )

    async def staff(self, *, screen_name: str | None, erase: bool = False) -> None:
        """Let the staff member Sanne exist, with or without a screen name."""
        engine = create_engine(self.settings.database_url)
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.save_staff(
                StaffMember(
                    SUBJECT,
                    "Sanne de Vries",
                    frozenset({Role.GROUP_ADMIN}),
                    frozenset({Membership("group_admin", "demo", "demo")}),
                    NOW,
                    NOW,
                    NOW,
                    screen_name=screen_name,
                )
            )
            if erase:
                await tx.erase_staff(SUBJECT, NOW)
        await engine.dispose()

    async def claim(self, claimed_by: str | None = None) -> None:
        """Let Sanne accept the booking (what the own queue writes), or empty the claim."""
        pseudonym = claimed_by or agent_pseudonym(agent_pseudonym_key(self.settings), SUBJECT)
        values = (
            {"claimed_by": pseudonym, "claimed_at": NOW}
            if claimed_by != ""
            else {
                "claimed_by": None,
                "claimed_at": None,
            }
        )
        app = self.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with app.state.engine.begin() as conn:
            await conn.execute(
                update(BookingRow).where(BookingRow.id == self.booking_id).values(**values)
            )


def _on(settings: Settings, **more: object) -> Settings:
    return settings.model_copy(update={"embedded_call_enabled": True, **more})


async def _site(settings: Settings) -> AsyncIterator[Site]:
    """Book and confirm, then open the participant's link inside the join window."""
    engine = create_engine(settings.database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, video_host_email="host@example.nl"))
    await engine.dispose()
    clock = FixedClock(NOW)
    async with app_client(settings, clock) as client:
        assert (await book(client)).status_code == 303
        token = await confirm_newest(client)
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with app.state.engine.connect() as conn:
            booking_id = int(
                (
                    await conn.execute(select(BookingRow.id).order_by(BookingRow.id.desc()))
                ).scalar_one()
            )
        engine = create_engine(settings.database_url)
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://m.example/guest"))
        await engine.dispose()
        clock.advance(START - timedelta(minutes=5) - NOW)
        base = await open_link(client, token)
        yield Site(client, settings, base, booking_id, clock)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    async for running in _site(_on(demo_settings)):
        yield running


# ---- what is answered ----------------------------------------------------------------------------


async def test_the_screen_name_of_who_accepted_is_the_answer_by_default(site: Site) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()

    response = await site.ask()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"name": "Sanne"}


async def test_without_a_screen_name_the_answer_is_empty_and_never_the_account_name(
    site: Site,
) -> None:
    await site.staff(screen_name=None)
    await site.claim()

    response = await site.ask()

    assert response.json() == {}
    assert "Sanne de Vries" not in response.text
    assert SUBJECT not in response.text


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("screen_name", {"name": "Sanne"}),
        ("name", {"name": "Sanne de Vries"}),
        ("term", {}),
    ],
)
async def test_the_organisations_setting_decides_what_the_answer_names(
    demo_settings: Settings, setting: str, expected: dict[str, str]
) -> None:
    async for running in _site(_on(demo_settings, embedded_call_staff_name=setting)):
        await running.staff(screen_name="Sanne")
        await running.claim()

        assert (await running.ask()).json() == expected


async def test_a_value_stored_before_the_screen_name_existed_keeps_working(
    demo_settings: Settings,
) -> None:
    # ``name`` was the only choice besides ``term`` until 2026-11-14: an organisation that stored it
    # keeps the account's name, and its default does not silently change under it.
    async for running in _site(_on(demo_settings, embedded_call_staff_name="name")):
        await running.staff(screen_name=None)
        await running.claim()

        assert (await running.ask()).json() == {"name": "Sanne de Vries"}


async def test_nobody_has_accepted_yet(site: Site) -> None:
    await site.staff(screen_name="Sanne")

    assert (await site.ask()).json() == {}


async def test_a_claim_nobody_here_can_name_is_empty(site: Site) -> None:
    await site.claim("f" * 64)  # a pseudonym that matches no staff record

    assert (await site.ask()).json() == {}


async def test_a_changed_screen_name_shows_at_the_next_ask(site: Site) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()
    assert (await site.ask()).json() == {"name": "Sanne"}

    await site.staff(screen_name="Sam")

    assert (await site.ask()).json() == {"name": "Sam"}


async def test_after_the_purge_there_is_no_claim_and_so_no_name(site: Site) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()
    assert (await site.ask()).json() == {"name": "Sanne"}

    await site.claim("")  # the purge empties claimed_by and claimed_at (app.domain.privacy)

    assert (await site.ask()).json() == {}


async def test_an_erased_staff_member_has_no_name(site: Site) -> None:
    await site.staff(screen_name="Sanne", erase=True)
    await site.claim()

    assert (await site.ask()).json() == {}


# ---- who may ask ---------------------------------------------------------------------------------


async def test_without_the_session_the_answer_is_the_one_for_a_booking_that_does_not_exist(
    site: Site,
) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()

    async with site.stranger() as stranger:
        refused = await stranger.get(site.path)
        unknown = await stranger.get("/b/m/no-such-ref/call/staff")

    assert refused.status_code == unknown.status_code == 403
    assert refused.text == unknown.text
    assert "Sanne" not in refused.text


async def test_another_bookings_session_gets_nothing(site: Site) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()
    async with site.stranger() as other:
        now = site.clock.now()
        site.clock.advance(NOW + timedelta(hours=12) - now)
        assert (await book(other, FRIDAY_0930)).status_code == 303
        theirs = await open_link(other, await confirm_newest(other))
        site.clock.advance(now - site.clock.now())
        assert theirs != site.base

        refused = await other.get(site.path)  # their cookie, our booking's address
        unknown = await other.get("/b/m/no-such-ref/call/staff")

    assert refused.status_code == unknown.status_code == 403
    assert refused.text == unknown.text
    assert "Sanne" not in refused.text


async def test_it_does_not_exist_while_the_switch_is_off(demo_settings: Settings) -> None:
    async for running in _site(demo_settings.model_copy(update={"embedded_call_enabled": False})):
        off = await running.ask()
        nothing = await running.client.get(f"{running.base}/no-such-page")

        assert off.status_code == nothing.status_code == 404
        assert off.text == nothing.text


async def test_it_answers_a_get_only(site: Site) -> None:
    assert (
        await site.client.post(site.path, headers={"origin": "http://testserver"})
    ).status_code == 405


# ---- the limit -----------------------------------------------------------------------------------


async def test_one_booking_is_held_by_a_limit_of_the_page_s_own_poll_rate_not_by_the_address(
    demo_settings: Settings,
) -> None:
    # The poll runs every 10 seconds: six asks a minute, so three times that is 18. The common read
    # limit of the address (3 a minute here) does not apply to this route: participants behind one
    # address do not share a bucket.
    settings = _on(
        demo_settings,
        rate_limit_enabled=True,
        rate_limit_reads="3/minute",
        embedded_call_chat_poll_seconds=10,
    )
    async for running in _site(settings):
        answers = [(await running.ask()).status_code for _ in range(19)]

        assert answers == [200] * 18 + [429]
        limited = await running.ask()
        assert limited.json() == {"error": "rate_limited"}  # a script's route answers JSON
        assert limited.headers["cache-control"] == "no-store"
        assert limited.headers["retry-after"] == "60"
        # Another participant at the same address has a bucket of their own.
        async with running.stranger() as other:
            now = running.clock.now()
            running.clock.advance(NOW + timedelta(hours=12) - now)
            assert (await book(other, FRIDAY_0930)).status_code == 303
            theirs = await open_link(other, await confirm_newest(other))
            running.clock.advance(now - running.clock.now())
            assert (await other.get(f"{theirs}/call/staff")).status_code == 200


# ---- what the answer holds -----------------------------------------------------------------------


async def test_the_answer_holds_a_name_or_nothing_and_no_identifier_of_the_staff_member(
    site: Site,
) -> None:
    await site.staff(screen_name="Sanne")
    await site.claim()
    pseudonym = agent_pseudonym(agent_pseudonym_key(site.settings), SUBJECT)

    response = await site.ask()

    assert set(response.json()) == {"name"}
    for leaked in (SUBJECT, pseudonym, "Sanne de Vries", "group_admin"):
        assert leaked not in response.text
        assert all(leaked not in value for value in response.headers.values())
