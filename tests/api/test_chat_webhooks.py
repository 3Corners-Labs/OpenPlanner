"""The chat's inbound deliveries through the gate of ADR 0124 (ADR 0123 point 8, step 7b).

Two paths carry a conversation: the asset's address
``/webhooks/contact-center/webex_cc/{org}/messages`` with a secret of its own, and the event
subscription at ``/webhooks/contact-center/webex_cc/{org}``. Both pass the one gate (signature,
time, replay, limits), are matched to the booking that owns the task and to nothing else, and
store a participant's or staff member's words: those words reach no log line and no refusal
event.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.webhooks import event_body, sign_wxcc
from app.config import Settings
from app.domain.authorization import Scope
from app.domain.call_chat import CallMessage, ChatAuthor, ChatState
from app.domain.call_chat_service import CallChatService
from app.domain.contact_center import (
    ContactCenterKind,
    ContactCenterTaskRef,
    OrganisationConnection,
    TaskKind,
)
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.services import BookingService
from app.domain.webhook_secrets import WebhookSecrets
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeContactCenterConnections, FakeOrganisationDispatchers
from tests.support.app_client import served
from tests.support.database import migrated_database
from tests.support.domain import NOW as BOOKED_AT
from tests.support.domain import SLOT_0900, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group
from tests.support.webhooks import NOW

ORG = "org-balie"
ADDRESS = f"/webhooks/contact-center/webex_cc/{ORG}"
MESSAGES = f"{ADDRESS}/messages"
MESSAGE_SECRET = "message-secret-0123456789abcdef"
WORDS = "mijn-privé-woorden-12345"


@dataclass
class Site:
    client: httpx.AsyncClient
    engine: AsyncEngine
    connections: FakeContactCenterConnections
    booking_id: int
    organisation_id: int

    def secret_of_events(self) -> str:
        return f"secret-of-{self.organisation_id}"

    async def thread(self) -> list[CallMessage]:
        async with SqlUnitOfWork(self.engine).read() as tx:
            return await tx.list_call_messages(self.booking_id)

    async def post(self, path: str, data: dict[str, Any], secret: str, **fields: Any) -> int:
        payload = {
            "id": fields.pop("event_id", "evt-1"),
            "type": fields.pop("type", "task-message:appended"),
            "comciscotimestamp": int(NOW.timestamp() * 1000),
            "data": {"taskId": "task-1", **data},
        }
        body = event_body(payload)
        headers = sign_wxcc(body, secret, int(NOW.timestamp() * 1000))
        response = await self.client.post(path, content=body, headers=headers)
        assert response.content == b""  # every answer of the gate is empty
        return response.status_code


def _agent_message(text: str = WORDS, alias: str = "m-1") -> dict[str, Any]:
    return {
        "messageDirection": "OUTBOUND",
        "senderType": "agent",
        "senderId": "agent-identifier-0001",
        "channelParams": {"type": "text", "message": {"aliasId": alias, "text": text}},
    }


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "chat-webhooks")


@asynccontextmanager
async def _site(
    settings: Settings, database_url: str, *, message_secrets: WebhookSecrets | None = None
) -> AsyncIterator[Site]:
    app = create_app(settings.model_copy(update={"database_url": database_url}), FixedClock(NOW))
    async with served(app) as client:
        engine: AsyncEngine = app.state.engine
        await seed_group(engine)
        uow = SqlUnitOfWork(engine)
        receipt = await book(
            BookingService(uow, FixedClock(BOOKED_AT), TEST_TOKEN_KEY, treatment_buffer=0),
            start=SLOT_0900,
        )
        booking_id = receipt.booking.id or 0
        async with uow.locked() as tx:
            await tx.save_task(
                booking_id,
                ContactCenterTaskRef("task-1", ContactCenterKind.WEBEX_CC, TaskKind.CONVERSATION),
            )
            group = await tx.get_group_by_id(receipt.booking.group_id)
        assert group is not None
        connections = FakeContactCenterConnections()
        connections.connections[group.organisation_id] = OrganisationConnection(
            ContactCenterKind.WEBEX_CC, "https://api.test", "client", True, True, NOW
        )
        if message_secrets is not None:
            connections.message_secrets_held[group.organisation_id] = message_secrets
        app.state.contact_center_connection_service = ContactCenterConnectionService(
            uow,
            FixedClock(NOW),
            app.state.admin_service,
            connections,
            lambda: FakeOrganisationDispatchers(),
        )
        app.state.call_chat_service = CallChatService(uow, lambda _: True)
        yield Site(client, engine, connections, booking_id, group.organisation_id)


def _made(secret: str = MESSAGE_SECRET, **fields: Any) -> WebhookSecrets:
    return WebhookSecrets(
        secret, rotated_at=fields.pop("rotated_at", NOW - timedelta(days=1)), **fields
    )


async def test_a_staff_message_at_the_asset_address_is_stored_for_its_booking(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        status = await site.post(MESSAGES, _agent_message(), MESSAGE_SECRET)

        stored = await site.thread()

    assert status == 204
    assert [(m.author, m.state, m.message_text) for m in stored] == [
        (ChatAuthor.STAFF, ChatState.DELIVERED, WORDS)
    ]
    assert stored[0].booking_id == site.booking_id


async def test_the_address_does_not_exist_until_its_secret_is_made(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url) as site:
        without = await site.post(MESSAGES, _agent_message(), "any-secret")
        unknown_org = await site.post(
            "/webhooks/contact-center/webex_cc/nobody/messages", _agent_message(), "any-secret"
        )
        other_platform = await site.post(
            f"/webhooks/contact-center/genesys/{ORG}/messages", _agent_message(), "any-secret"
        )
        stored = await site.thread()

    assert (without, unknown_org, other_platform) == (404, 404, 404)
    assert stored == []


async def test_the_events_secret_does_not_open_the_message_address_and_the_reverse(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        events_secret_at_messages = await site.post(
            MESSAGES, _agent_message(), site.secret_of_events()
        )
        message_secret_at_events = await site.post(ADDRESS, _agent_message(), MESSAGE_SECRET)
        stored = await site.thread()

    assert (events_secret_at_messages, message_secret_at_events) == (401, 401)
    assert stored == []


async def test_a_replay_is_one_message_and_a_second_delivery_of_the_same_message_too(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        first = await site.post(MESSAGES, _agent_message(), MESSAGE_SECRET)
        replay = await site.post(MESSAGES, _agent_message(), MESSAGE_SECRET)  # the same event
        # The same message under another event id, as when the subscription reports it too.
        twin = await site.post(
            ADDRESS, _agent_message(), site.secret_of_events(), event_id="evt-other"
        )

        stored = await site.thread()

    assert (first, replay, twin) == (204, 204, 204)
    assert len(stored) == 1


async def test_a_stale_delivery_is_refused_before_anything_is_stored(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        payload = {
            "id": "old",
            "type": "task-message:appended",
            "comciscotimestamp": int((NOW - timedelta(seconds=301)).timestamp() * 1000),
            "data": {"taskId": "task-1", **_agent_message()},
        }
        body = event_body(payload)
        headers = sign_wxcc(body, MESSAGE_SECRET, payload["comciscotimestamp"])  # type: ignore[arg-type]

        response = await site.client.post(MESSAGES, content=body, headers=headers)
        stored = await site.thread()

    assert (response.status_code, response.content) == (401, b"")
    assert stored == []


async def test_the_previous_message_secret_verifies_inside_the_overlap_and_not_after_it(
    settings: Settings, database_url: str
) -> None:
    just_rotated = _made(
        "new-secret", previous="old-secret", rotated_at=NOW - timedelta(minutes=10)
    )
    long_ago = _made("new-secret", previous="old-secret", rotated_at=NOW - timedelta(hours=3))

    async with _site(settings, database_url, message_secrets=just_rotated) as site:
        inside = await site.post(MESSAGES, _agent_message(alias="a"), "old-secret")
        site.connections.message_secrets_held[site.organisation_id] = long_ago
        after = await site.post(MESSAGES, _agent_message(alias="b"), "old-secret")
        current = await site.post(MESSAGES, _agent_message(alias="c"), "new-secret")

    assert (inside, after, current) == (204, 401, 204)


async def test_the_subscription_confirms_the_participants_message_and_reports_the_refusal(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url) as site:
        async with SqlUnitOfWork(site.engine).locked() as tx:
            for name in ("ours-1", "ours-2"):
                await tx.add_call_message(
                    CallMessage(
                        site.booking_id,
                        ChatAuthor.PARTICIPANT,
                        WORDS,
                        NOW,
                        ChatState.SENT,
                        name,
                    )
                )
        confirmed = await site.post(
            ADDRESS,
            {
                "messageDirection": "INBOUND",
                "channelParams": {"message": {"aliasId": "ours-1", "text": WORDS}},
            },
            site.secret_of_events(),
            event_id="evt-c",
        )
        after_the_end = await site.post(
            ADDRESS,
            {"reason": "NOT_FOUND"},
            site.secret_of_events(),
            type="task-message:append-failed",
            event_id="evt-f",
        )
        states = {m.provider_message_id: m.state for m in await site.thread()}

    assert (confirmed, after_the_end) == (204, 204)
    assert states == {"ours-1": ChatState.DELIVERED, "ours-2": ChatState.REFUSED}


async def test_a_paused_connection_drops_the_message_with_the_same_answer(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        stored = site.connections.connections[site.organisation_id]
        await site.connections.set_inbound_enabled(site.organisation_id, False, NOW)
        status = await site.post(MESSAGES, _agent_message(), MESSAGE_SECRET)
        paused = await site.thread()
        assert stored.inbound_enabled

    assert status == 204
    assert paused == []


async def test_the_installation_wide_address_has_no_booking_of_its_own_so_it_stores_nothing(
    settings: Settings, database_url: str
) -> None:
    secured = settings.model_copy(
        update={"wxcc_webhook_secret": __import__("pydantic").SecretStr("installation-secret")}
    )
    async with _site(secured, database_url) as site:
        status = await site.post("/webhooks/wxcc", _agent_message(), "installation-secret")
        stored = await site.thread()

    assert status == 204
    assert stored == []


async def test_the_words_reach_no_log_line_and_no_refusal_event(
    settings: Settings, database_url: str
) -> None:
    async with _site(settings, database_url, message_secrets=_made()) as site:
        with structlog.testing.capture_logs() as lines:
            await site.post(MESSAGES, _agent_message(), MESSAGE_SECRET)  # accepted
            await site.post(MESSAGES, _agent_message(), "forged-secret")  # refused
            await site.post(ADDRESS, _agent_message(), "forged-secret")  # refused
        async with SqlUnitOfWork(site.engine).read() as tx:
            events = await tx.list_audit_events(Scope(everything=True))

    assert any(line["event"] == "webhook.chat" for line in lines)
    assert WORDS not in json.dumps(lines, default=str)
    assert WORDS not in json.dumps([vars(e.event) for e in events], default=str)
    assert "agent-identifier-0001" not in json.dumps(lines, default=str)
