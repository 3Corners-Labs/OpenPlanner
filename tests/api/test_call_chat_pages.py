"""The chat beside the call: its read and its send, and their abuse cases (ADR 0123 step 7c).

Every abuse case of the threat model's chat section has a test here: sending as another booking,
sending and reading without a session, markup in a message, flooding, a very long message,
replaying a send, reading another booking's thread, a form post from another site, a service
without a chat, the switch off, and the words of a message in a log. The text is personal data:
the last test looks for it in the log.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog.testing
from sqlalchemy import select, update

from app.adapters.db.engine import create_engine
from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.authorization import Role
from app.domain.booking import MeetingRef
from app.domain.call_chat import (
    CallMessage,
    ChatAuthor,
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatSender,
    ChatState,
)
from app.domain.call_chat_service import CallChatService
from app.domain.calls import CallStatus, TaskState
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.contact_center_dispatch import DispatcherChoice
from app.domain.integration import EventOutcome
from app.domain.ports import IntegrationError
from app.domain.staff import Membership, StaffMember
from app.domain.tokens import agent_pseudonym
from app.keys import agent_pseudonym_key
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, webex_cc
from tests.support.call_page import Page, embedded_app_client
from tests.support.domain import NOW
from tests.support.participant import (
    FRIDAY_0900,
    FRIDAY_0930,
    book,
    confirm_newest,
    open_link,
)

START = datetime.fromisoformat(FRIDAY_0900.replace("Z", "+00:00"))
WORDS = "mijn-privé-woorden-12345"
ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
OTHER = "0f9e8d7c6b5a49382716f5e4d3c2b1a0"
SUBJECT = "staff-sanne"
SAME_SITE = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


@dataclass
class Site:
    """A confirmed booking of Ada whose task is a conversation, and the fake platform."""

    client: httpx.AsyncClient
    settings: Settings
    base: str
    booking_id: int
    clock: FixedClock
    dispatcher: FakeDispatcher

    @property
    def path(self) -> str:
        """The chat's address of the booking."""
        return f"{self.base}/call/chat"

    async def read(self, query: str = "") -> httpx.Response:
        return await self.client.get(self.path + query)

    async def send(self, text: str = WORDS, client_id: str = ID, **headers: str) -> httpx.Response:
        return await self.post({"id": client_id, "text": text}, **headers)

    async def post(self, body: Any, **headers: str) -> httpx.Response:
        return await self.client.post(self.path, json=body, headers={**SAME_SITE, **headers})

    def stranger(self) -> httpx.AsyncClient:
        """A browser without the participant's cookie."""
        return httpx.AsyncClient(
            transport=self.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        )

    @property
    def app(self) -> Any:
        return self.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def stored(self) -> list[CallMessage]:
        async with SqlUnitOfWork(self.app.state.engine).read() as tx:
            return await tx.messages_of_call(self.booking_id)

    async def deliver(self, event: ChatEvent, key: str) -> EventOutcome:
        service: CallChatService = self.app.state.call_chat_service
        return await service.record(
            event,
            self.clock.now(),
            provider=ContactCenterKind.WEBEX_CC,
            source="wxcc",
            organisation_id=1,
            event_key=key,
            scope="organisation:1",
            body_identity=key,
        )

    async def staff_says(self, text: str, message_id: str = "m-1") -> None:
        event = ChatEvent(
            ChatEventKind.APPENDED,
            "task-1",
            ChatDirection.OUTBOUND,
            message_id=message_id,
            message_text=text,
            at=self.clock.now(),
        )
        event = replace(event, sender=ChatSender.AGENT)
        assert await self.deliver(event, message_id) is EventOutcome.RECORDED

    async def confirm(self, message_id: int) -> None:
        async with SqlUnitOfWork(self.app.state.engine).locked() as tx:
            stored = next(
                m for m in await tx.messages_of_call(self.booking_id) if m.id == message_id
            )
            await tx.confirm_call_message(self.booking_id, stored.provider_message_id)

    async def staff_member(self, screen_name: str | None) -> None:
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
        await engine.dispose()
        pseudonym = agent_pseudonym(agent_pseudonym_key(self.settings), SUBJECT)
        async with self.app.state.engine.begin() as conn:
            await conn.execute(
                update(BookingRow)
                .where(BookingRow.id == self.booking_id)
                .values(claimed_by=pseudonym, claimed_at=NOW)
            )


def _on(settings: Settings, **more: object) -> Settings:
    return settings.model_copy(update={"embedded_call_enabled": True, **more})


async def _site(
    settings: Settings, task_kind: TaskKind = TaskKind.CONVERSATION
) -> AsyncIterator[Site]:
    """Book and confirm, give the booking a task of ``task_kind``, open the participant's link."""
    engine = create_engine(settings.database_url)
    async with SqlUnitOfWork(engine).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group(replace(group, video_host_email="host@example.nl"))
    await engine.dispose()
    clock = FixedClock(NOW)
    async with embedded_app_client(settings, clock) as client:
        assert (await book(client)).status_code == 303
        token = await confirm_newest(client)
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with app.state.engine.connect() as conn:
            booking_id = int(
                (
                    await conn.execute(select(BookingRow.id).order_by(BookingRow.id.desc()))
                ).scalar_one()
            )
        uow = SqlUnitOfWork(app.state.engine)
        async with uow.locked() as tx:
            await tx.save_meeting(booking_id, MeetingRef("meeting-1", "https://m.example/guest"))
            await tx.save_task(
                booking_id, ContactCenterTaskRef("task-1", ContactCenterKind.WEBEX_CC, task_kind)
            )
        dispatcher = FakeDispatcher()
        app.state.call_chat_service = CallChatService(
            uow, lambda _: True, lambda: DispatcherChoice(webex_cc(dispatcher)), clock
        )
        clock.advance(START - timedelta(minutes=5) - NOW)
        base = await open_link(client, token)
        yield Site(client, settings, base, booking_id, clock, dispatcher)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    async for running in _site(_on(demo_settings)):
        yield running


# ---- sending ------------------------------------------------------------------------------------


async def test_a_message_is_sent_to_the_task_and_answered_as_the_stored_message(
    site: Site,
) -> None:
    response = await site.send()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    message = response.json()["message"]
    assert (message["author"], message["state"], message["text"]) == ("participant", "sent", WORDS)
    assert isinstance(message["id"], int)
    assert message["time"]  # worded in the group's time zone, not an instant for the page to format
    assert [(task, m.message_id, m.text) for task, m in site.dispatcher.appended] == [
        ("task-1", f"p{ID}", WORDS)
    ]


async def test_the_platforms_confirmation_shows_as_delivered_at_the_next_read(site: Site) -> None:
    sent = (await site.send()).json()["message"]
    confirmation = ChatEvent(
        ChatEventKind.APPENDED, "task-1", ChatDirection.INBOUND, message_id=f"p{ID}"
    )
    assert await site.deliver(confirmation, "k-1") is EventOutcome.RECORDED

    read = await site.read(f"?since={sent['id']}&waiting={sent['id']}")

    assert read.json()["states"] == {str(sent["id"]): "delivered"}
    assert read.json()["messages"] == []


async def test_a_replayed_send_is_the_same_message_and_reaches_the_platform_once(
    site: Site,
) -> None:
    first = (await site.send()).json()["message"]
    again = (await site.send()).json()["message"]

    assert again["id"] == first["id"]
    assert len(await site.stored()) == 1
    assert len(site.dispatcher.appended) == 1


@pytest.mark.parametrize(
    ("text", "error"),
    [("", "empty"), ("   \n ", "empty"), ("x" * 1001, "too_long")],
)
async def test_an_empty_or_a_very_long_message_is_refused_with_its_code(
    site: Site, text: str, error: str
) -> None:
    response = await site.send(text)

    assert response.status_code == 400
    assert response.json() == {"error": error}
    assert await site.stored() == []
    assert site.dispatcher.appended == []


async def test_a_body_the_page_would_not_make_is_refused(site: Site) -> None:
    refused = [
        await site.post({"id": ID}),  # no text
        await site.post({"text": WORDS}),  # no identifier
        await site.post({"id": 5, "text": WORDS}),  # an identifier that is no text
        await site.post({"id": ID, "text": ["a"]}),  # a text that is a list
        await site.post(["a"]),  # not an object
        await site.send(client_id="not an id"),
        await site.client.post(
            site.path,
            content=b"not json",
            headers={**SAME_SITE, "content-type": "application/json"},
        ),
        await site.client.post(
            site.path,
            content=b'{"id":"x","text":"y"}',
            headers={**SAME_SITE, "content-type": "text/plain"},
        ),
        await site.client.post(
            site.path,
            content=b'{"id":"' + ID.encode() + b'","text":"' + b"a" * 40_000 + b'"}',
            headers={**SAME_SITE, "content-type": "application/json"},
        ),
    ]

    assert [r.status_code for r in refused] == [400] * len(refused)
    assert {r.json()["error"] for r in refused} == {"bad_request"}
    assert site.dispatcher.appended == []


async def test_markup_in_a_message_is_text_everywhere_it_is_given_back(site: Site) -> None:
    markup = "<img src=x onerror=alert(1)> <b>vet</b> &amp;"

    sent = await site.send(markup)
    read = await site.read()
    page = await site.client.get(f"{site.base}/call")

    assert sent.json()["message"]["text"] == markup  # a JSON string: the script sets textContent
    assert read.json()["messages"][0]["text"] == markup
    assert read.headers["content-type"] == "application/json"
    assert "<img src=x" not in page.text  # the page holds no message: the script asks for them
    assert "nosniff" in read.headers.get("x-content-type-options", "")


async def test_a_flood_stops_at_the_setting_and_says_when_to_come_back(
    demo_settings: Settings,
) -> None:
    async for running in _site(_on(demo_settings, embedded_call_chat_per_minute=5)):
        answers = [await running.send(f"bericht {n}", client_id=f"{n:016d}") for n in range(7)]

        assert [a.status_code for a in answers] == [200] * 5 + [429] * 2
        assert answers[5].json() == {"error": "rate_limited"}
        assert answers[5].headers["retry-after"]
        assert len(running.dispatcher.appended) == 5  # the platform never saw the others
        running.clock.advance(timedelta(minutes=1, seconds=1))
        assert (await running.send("weer", client_id="f" * 16)).status_code == 200


async def test_the_organisations_longest_message_decides(demo_settings: Settings) -> None:
    async for running in _site(_on(demo_settings, embedded_call_chat_max_chars=200)):
        assert (await running.send("a" * 200)).status_code == 200
        assert (await running.send("a" * 201, client_id="b" * 16)).json() == {"error": "too_long"}


async def test_a_platform_that_cannot_be_reached_keeps_nothing_and_the_retry_works(
    site: Site,
) -> None:
    site.dispatcher.append_error = IntegrationError("down", retryable=True)

    failed = await site.send()

    assert failed.status_code == 503
    assert failed.json() == {"error": "unavailable"}
    assert await site.stored() == []

    site.dispatcher.append_error = None
    assert (await site.send()).status_code == 200


async def test_a_chat_that_is_closed_takes_nothing(site: Site) -> None:
    await site.send()
    async with SqlUnitOfWork(site.app.state.engine).locked() as tx:
        await tx.refuse_sent_call_messages(site.booking_id)

    refused = await site.send("nog een", client_id="c" * 16)

    assert refused.status_code == 409
    assert refused.json() == {"error": "closed"}


# ---- reading ------------------------------------------------------------------------------------


async def test_a_read_has_the_thread_in_order_with_the_staff_members_screen_name(
    site: Site,
) -> None:
    await site.staff_member("Sanne")
    mine = (await site.send("vraag")).json()["message"]
    await site.staff_says("antwoord")

    read = await site.read()

    assert read.status_code == 200
    assert read.headers["cache-control"] == "no-store"
    body = read.json()
    assert [(m["author"], m["text"], m["state"]) for m in body["messages"]] == [
        ("participant", "vraag", "sent"),
        ("staff", "antwoord", "delivered"),
    ]
    assert body["messages"][0]["id"] == mine["id"]
    assert body["staff"] == "Sanne"
    assert body["closed"] is None
    assert "Sanne de Vries" not in read.text  # never the account's name by default
    assert SUBJECT not in read.text


async def test_a_read_that_brings_nothing_carries_no_message(site: Site) -> None:
    sent = (await site.send()).json()["message"]
    await site.staff_says("antwoord")
    first = (await site.read()).json()
    cursor = max(m["id"] for m in first["messages"])

    nothing = await site.read(f"?since={cursor}")

    assert nothing.json() == {
        "messages": [],
        "states": {},
        "staff": "",
        "closed": None,
        "more": False,
    }
    assert WORDS not in nothing.text
    assert sent["id"] <= cursor


async def test_a_closed_chat_says_why(site: Site) -> None:
    await site.send()
    async with SqlUnitOfWork(site.app.state.engine).locked() as tx:
        await tx.refuse_sent_call_messages(site.booking_id)

    assert (await site.read()).json()["closed"] == "staff"


@pytest.mark.parametrize(
    "query",
    [
        "?since=x",
        "?since=-5",
        "?waiting=a,b,,99999999999999999999999",
        "?since=1&waiting=" + "1," * 500,
    ],
)
async def test_a_query_the_page_would_not_make_never_breaks_the_read(
    site: Site, query: str
) -> None:
    response = await site.read(query)

    assert response.status_code in (200, 422)


# ---- who may ask --------------------------------------------------------------------------------


async def test_without_the_session_a_read_and_a_send_are_refused_as_for_an_unknown_booking(
    site: Site,
) -> None:
    await site.send()

    async with site.stranger() as stranger:
        read = await stranger.get(site.path)
        write = await stranger.post(site.path, json={"id": OTHER, "text": WORDS}, headers=SAME_SITE)
        unknown = await stranger.get("/b/m/no-such-ref/call/chat")

    assert read.status_code == write.status_code == unknown.status_code == 403
    assert read.text == unknown.text
    assert WORDS not in read.text
    assert len(await site.stored()) == 1  # the outsider's send stored and sent nothing


async def test_another_bookings_session_cannot_read_or_write_this_chat(site: Site) -> None:
    await site.send()
    async with site.stranger() as other:
        now = site.clock.now()
        site.clock.advance(NOW + timedelta(hours=12) - now)
        assert (await book(other, FRIDAY_0930)).status_code == 303
        await open_link(other, await confirm_newest(other))
        site.clock.advance(now - site.clock.now())

        read = await other.get(site.path)  # their cookie, our booking's address
        write = await other.post(
            site.path, json={"id": OTHER, "text": "ik ben een ander"}, headers=SAME_SITE
        )

    assert read.status_code == write.status_code == 403
    assert WORDS not in read.text
    assert [m.message_text for m in await site.stored()] == [WORDS]
    assert len(site.dispatcher.appended) == 1


async def test_a_post_from_another_site_is_refused_before_anything_happens(site: Site) -> None:
    forged = await site.send(origin="https://elsewhere.example", **{"sec-fetch-site": "cross-site"})

    assert forged.status_code == 403
    assert await site.stored() == []
    assert site.dispatcher.appended == []


async def test_it_does_not_exist_while_the_switch_is_off(demo_settings: Settings) -> None:
    async for running in _site(demo_settings.model_copy(update={"embedded_call_enabled": False})):
        off = await running.read()
        nothing = await running.client.get(f"{running.base}/no-such-page")
        write = await running.send()

        assert off.status_code == write.status_code == nothing.status_code == 404
        assert off.text == nothing.text


async def test_a_booking_without_a_chat_has_neither_the_routes_nor_the_side_bar(
    demo_settings: Settings,
) -> None:
    async for running in _site(_on(demo_settings), TaskKind.FORM):
        read = await running.read()
        write = await running.send()
        page = await running.client.get(f"{running.base}/call")

        assert read.status_code == write.status_code == 404
        assert page.status_code == 200
        assert "data-call-chat" not in page.text
        assert "call-chat" not in page.text
        assert running.dispatcher.appended == []


async def test_it_answers_a_get_and_a_post_only(site: Site) -> None:
    assert (await site.client.put(site.path, headers=SAME_SITE)).status_code == 405
    assert (await site.client.delete(site.path, headers=SAME_SITE)).status_code == 405


# ---- the page ----------------------------------------------------------------------------------


async def test_the_page_of_a_booking_with_a_chat_carries_the_side_bar_and_nothing_secret(
    site: Site,
) -> None:
    await site.send()
    html = (await site.client.get(f"{site.base}/call")).text
    page = Page(html)

    side = page.element("data-call-chat")
    assert side.hidden  # shown by the script, from the state after the start
    assert 'role="log"' in side.raw
    assert 'aria-live="polite"' in side.raw
    assert "data-call-chat-form" in side.raw
    assert f'data-call-chat-url="{site.base}/call/chat"' in html
    assert 'data-call-chat-max="1000"' in html
    assert WORDS not in html  # the thread is asked for, never put in the page


async def test_the_chat_stays_said_to_be_available_where_the_picture_fails_or_is_refused(
    site: Site,
) -> None:
    html = (await site.client.get(f"{site.base}/call")).text

    assert html.count("De chat blijft beschikbaar.") == 3  # camera, microphone, both
    assert (
        html.count("U kunt ons ook bereiken via de chat naast deze pagina.") == 2
    )  # failed, unavailable


async def test_a_booking_without_a_chat_says_nothing_about_one(demo_settings: Settings) -> None:
    async for running in _site(_on(demo_settings), TaskKind.FORM):
        html = (await running.client.get(f"{running.base}/call")).text

        assert "chat" not in html.lower()


async def test_the_page_says_the_same_in_english(site: Site) -> None:
    html = (await site.client.get(f"{site.base}/call?lang=en")).text

    assert "The chat stays available." in html
    assert "Chat with our" in html
    assert "Send" in html


# ---- what never reaches a log ----------------------------------------------------------------


async def test_the_words_of_a_message_reach_no_log_line_not_even_when_the_platform_fails(
    site: Site,
) -> None:
    with structlog.testing.capture_logs() as logs:
        await site.send()
        site.dispatcher.append_error = IntegrationError("down", retryable=True)
        await site.send("tweede-" + WORDS, client_id="d" * 16)
        await site.staff_says("antwoord-" + WORDS)
        await site.read()
        await site.send("", client_id="e" * 16)

    assert WORDS not in repr(logs)


# ---- the limit of the read ------------------------------------------------------------------


async def test_the_read_has_the_per_booking_bound_of_the_poll_and_names_nothing(
    demo_settings: Settings,
) -> None:
    # The poll runs every 10 seconds: six asks a minute, three times that is 18 a booking, counted
    # in a bucket of the chat's own (the name's has its own). The common read limit of the address
    # (3 a minute here) does not hold this route.
    settings = _on(
        demo_settings,
        rate_limit_enabled=True,
        rate_limit_reads="3/minute",
        embedded_call_chat_poll_seconds=10,
    )
    async for running in _site(settings):
        answers = [await running.read() for _ in range(19)]

        assert [a.status_code for a in answers] == [200] * 18 + [429]
        assert answers[-1].json() == {"error": "rate_limited"}
        assert answers[-1].headers["cache-control"] == "no-store"
        assert answers[-1].headers["retry-after"] == "60"
        # The bucket of the name is a different one: the staff poll still answers.
        assert (await running.client.get(f"{running.base}/call/staff")).status_code == 200


# ---- queries and bodies the page would not make ---------------------------------------------


@pytest.mark.parametrize("waiting", ["\u00b2", "1,\u00b2,3", "\u0663", "1;2", ",,,", "9" * 400])
async def test_a_waiting_list_with_digits_that_are_not_ascii_never_breaks_the_read(
    site: Site, waiting: str
) -> None:
    # ``str.isdigit`` accepts a superscript, which ``int`` refuses: that was a 500.
    response = await site.client.get(site.path, params={"waiting": waiting})

    assert response.status_code == 200
    assert response.json()["states"] == {}


async def test_deeply_nested_json_is_a_refused_body_and_not_a_server_error(site: Site) -> None:
    nested = b"[" * 30_000  # json.loads raises RecursionError for this, not ValueError

    response = await site.client.post(
        site.path, content=nested, headers={**SAME_SITE, "content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json() == {"error": "bad_request"}
    assert site.dispatcher.appended == []


async def test_only_the_participants_own_waiting_ids_are_answered(site: Site) -> None:
    mine = (await site.send()).json()["message"]
    await site.staff_says("antwoord")
    theirs = (await site.read()).json()["messages"][-1]["id"]
    await site.confirm(mine["id"])

    states = (await site.read(f"?waiting={mine['id']},{theirs}")).json()["states"]

    assert states == {str(mine["id"]): "delivered"}  # a staff message's id is never answered


# ---- a thread of many messages ----------------------------------------------------------------


async def test_a_read_is_cut_and_says_there_is_more_and_the_cursor_reads_the_rest(
    site: Site,
) -> None:
    async with SqlUnitOfWork(site.app.state.engine).locked() as tx:
        for number in range(105):
            await tx.add_call_message(
                CallMessage(
                    site.booking_id,
                    ChatAuthor.STAFF,
                    f"bericht {number}",
                    site.clock.now(),
                    ChatState.DELIVERED,
                    f"m-{number}",
                )
            )

    first = (await site.read()).json()
    rest = (await site.read(f"?since={first['messages'][-1]['id']}")).json()

    assert (len(first["messages"]), first["more"]) == (100, True)
    assert (len(rest["messages"]), rest["more"]) == (5, False)
    assert [m["text"] for m in first["messages"] + rest["messages"]] == [
        f"bericht {n}" for n in range(105)
    ]  # in order, none twice


# ---- the time of a message ----------------------------------------------------------------------


async def test_a_message_from_another_day_carries_its_date_and_one_from_today_does_not(
    site: Site,
) -> None:
    async with SqlUnitOfWork(site.app.state.engine).locked() as tx:
        await tx.add_call_message(
            CallMessage(
                site.booking_id,
                ChatAuthor.STAFF,
                "gisteren",
                site.clock.now() - timedelta(days=3),
                ChatState.DELIVERED,
                "m-old",
            )
        )
    await site.send("vandaag")

    old, today = (await site.read()).json()["messages"]

    assert " " not in today["time"].strip()  # the clock only
    assert old["time"].endswith(today["time"])  # the same hour, three days before ...
    assert old["time"] != today["time"]  # ... with its date in front
    assert any(ch.isdigit() for ch in old["time"].split(" ")[0])  # a date in front
    assert old["at"].startswith("20")  # the machine-readable value for the page's time element


# ---- why a chat is closed -----------------------------------------------------------------------


async def test_a_task_that_failed_is_a_chat_that_did_not_go_ahead_and_not_one_the_staff_closed(
    site: Site,
) -> None:
    async with SqlUnitOfWork(site.app.state.engine).locked() as tx:
        await tx.save_call_status(CallStatus(site.booking_id, task=TaskState.FAILED))

    assert (await site.read()).json()["closed"] == "lost"
    nl = (await site.client.get(f"{site.base}/call?lang=nl")).text
    assert "omdat het gesprek niet is doorgegaan" in nl
    assert "omdat het gesprek niet is doorgegaan" not in "Uw medewerker heeft de chat gesloten"


# ---- the attempts, not only the stored messages -------------------------------------------------


async def test_a_platform_that_is_down_is_not_asked_without_end(demo_settings: Settings) -> None:
    # Messages taken back leave no row, so the domain's rate does not see them: the attempts of the
    # booking are bounded on their own, at twice the per-minute setting (10 at 5).
    settings = _on(demo_settings, rate_limit_enabled=True, embedded_call_chat_per_minute=5)
    async for running in _site(settings):
        running.dispatcher.append_error = IntegrationError("rejected_403", retryable=False)

        answers = [await running.send(f"poging {n}", client_id=f"{n:016d}") for n in range(12)]

        assert [a.status_code for a in answers] == [503] * 10 + [429] * 2
        assert answers[-1].headers["retry-after"]
        assert await running.stored() == []
