"""Alert senders: email through a mail channel, a Webex space through a bot (ADR 0082 point 9)."""

import json

import httpx
import pytest

from app.adapters.alert_senders import (
    BotTokens,
    ConfiguredAlertSenders,
    EmailAlertSender,
    WebexSpaceSender,
    bot_name,
)
from app.domain.alert_model import AlertChannelKind, AlertTarget, RenderedAlert
from app.domain.channels import ChannelKind
from app.domain.ports import IntegrationError, OutboundMessage, RequestRecord

ALERT = RenderedAlert("Subject", "Plain text", "**Subject**\n\nPlain text")
ROOM = "Y2lzY29zcGFyazovL3VzL1JPT00v"


class Mail:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[OutboundMessage] = []
        self.error = error

    async def send(self, message: OutboundMessage) -> None:
        if self.error:
            raise self.error
        self.sent.append(message)


class Log:
    def __init__(self) -> None:
        self.records: list[RequestRecord] = []

    async def record(self, record: RequestRecord) -> None:
        self.records.append(record)


class Secrets:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def get(self, name: str) -> dict[str, object] | None:
        return self.values.get(name)

    async def put(self, name: str, value: dict[str, object], now: object) -> None:
        self.values[name] = value


async def test_email_goes_out_as_plain_text_and_failures_become_integration_errors() -> None:
    mail = Mail()
    await EmailAlertSender(mail).send("ops@voorbeeld.nl", ALERT)
    (message,) = mail.sent
    assert (message.channel, message.recipient, message.subject, message.text) == (
        ChannelKind.EMAIL,
        "ops@voorbeeld.nl",
        "Subject",
        "Plain text",
    )
    with pytest.raises(IntegrationError) as failed:
        await EmailAlertSender(Mail(OSError("smtp down"))).send("ops@voorbeeld.nl", ALERT)
    assert failed.value.code == "mail_failed"


async def test_a_webex_space_gets_markdown_with_the_bot_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "m1"}, headers={"trackingid": "T1"})

    log = Log()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await WebexSpaceSender(http, "bot-token", "https://webexapis.com/v1", log).send(ROOM, ALERT)
    (request,) = seen
    assert str(request.url) == "https://webexapis.com/v1/messages"
    assert request.headers["authorization"] == "Bearer bot-token"
    assert json.loads(request.content) == {"roomId": ROOM, "markdown": ALERT.markdown}
    (record,) = log.records
    assert (record.method, record.path, record.status, record.platform) == (
        "POST",
        "/v1/messages",
        200,
        None,
    )


async def test_a_refusing_space_is_an_error_code_without_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "room not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(IntegrationError) as refused:
            await WebexSpaceSender(http, "t", "https://webexapis.com").send(ROOM, ALERT)
    assert refused.value.code.startswith("rejected")
    assert "room not found" not in str(refused.value)


async def test_the_sender_follows_the_targets_kind_and_scope() -> None:
    org_mail, fallback = Mail(), Mail()

    class Directory:
        async def sender(self, organisation_id: int, channel: ChannelKind) -> Mail | None:
            return org_mail if organisation_id == 1 else None

    secrets = Secrets()
    bots = BotTokens(secrets)
    async with httpx.AsyncClient() as http:
        senders = ConfiguredAlertSenders(
            Directory(), lambda: fallback, bots, http, "https://webexapis.com"
        )
        email = AlertTarget(1, 1, AlertChannelKind.EMAIL, "ops@voorbeeld.nl")
        platform = AlertTarget(2, None, AlertChannelKind.EMAIL, "ops@voorbeeld.nl")
        space = AlertTarget(3, 1, AlertChannelKind.WEBEX_SPACE, ROOM)
        assert isinstance(await senders.sender(email), EmailAlertSender)
        assert isinstance(await senders.sender(platform), EmailAlertSender)
        assert await senders.sender(space) is None  # no bot token yet
        await bots.save(1, "x" * 30, None)  # type: ignore[arg-type]
        assert isinstance(await senders.sender(space), WebexSpaceSender)
        assert secrets.values[bot_name(1)] == {"token": "x" * 30}
        await bots.save(1, None, None)  # type: ignore[arg-type]
        assert not await bots.has_token(1)
        assert bot_name(None) == "webex-bot:platform"
